# -*- coding: utf-8 -*-
"""
对话摘要压缩（长对话上下文治理）
================================
解决问题：LangGraph 每个节点执行后 Checkpointer 全量快照 State，messages 按
add_messages 语义只增不减——长对话下 checkpoint 体积与 LLM prompt 线性膨胀，
最终撞上下文窗口 / 成本失控（详见 docs/context-compression.md）。

方案（增量式摘要压缩）：
  1. State 增设独立字段 conversation_summary（不混入 messages，避免被 reducer 反复处理）
  2. 顶层图 START 后插入 maybe_compress_history 节点：messages 超阈值时，
     把「最近 KEEP_RECENT 条以外」的历史与旧摘要一起喂 LLM，生成新摘要
  3. 被压缩的消息用 RemoveMessage 删除（add_messages 按 id 合并，
     直接返回裁剪后的 list 不会删除旧消息——必须显式 RemoveMessage）
  4. 增量：每次只压缩「上次摘要之后的新消息」，旧摘要作为上下文传入，不重复全文压缩
  5. 兜底：LLM 不可达/失败 → 不压缩（保留原样），打警告；丢消息比丢上下文安全

阈值（可按业务调整）：
  MAX_MESSAGES = 20     条数上限
  MAX_TOKENS   = 4000   估算 token 上限（粗略估算，不引 tiktoken）
  KEEP_RECENT  = 6      保留最近 6 条原文（约 3 轮对话）
"""
from __future__ import annotations

import logging
import unicodedata
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

from ..config import settings
from ..llm import llm_client
from .state import ResearchState

logger = logging.getLogger("fundagent.summarize")

MAX_MESSAGES = 20
MAX_TOKENS = 4000
KEEP_RECENT = 6

_SUMMARY_SYSTEM = (
    "你是对话摘要器，把基金研究对话的历史压缩成结构化摘要。"
    "严格要求：只基于给定内容提炼，不得推断、补充或编造任何未出现的信息；"
    "必须保留：用户的研究需求与标的、已得出的结论（评级/目标价/风险等级）、"
    "悬而未决的问题；忽略寒暄与重复。输出 150 字以内的连贯文本，不要分点。"
)


# ---------------------------------------------------------------------------
# 控制台报告：让压缩过程的数据变化可见（教学/调试用）
# ---------------------------------------------------------------------------

def _disp_width(text: str) -> int:
    """终端显示宽度：中文/全角字符占 2 列，其余占 1 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


def _truncate(text: str, maxlen: int = 38) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= maxlen else text[: maxlen - 1] + "…"


def _role_name(m: BaseMessage) -> str:
    return {HumanMessage: "用户", AIMessage: "助手", SystemMessage: "系统"}.get(
        type(m), getattr(m, "role", "消息"))


def _msg_lines(messages: list[BaseMessage], max_show: int = 8) -> list[str]:
    """消息预览行：过多时折叠中间（前 4 + … + 后 4）。"""
    if len(messages) <= max_show:
        shown = list(enumerate(messages, 1))
        lines = [f"  ├─ [{_role_name(m)}] {_truncate(m.content)}" for _, m in shown]
        if lines:
            lines[-1] = lines[-1].replace("├─", "└─", 1)
        return lines
    head, tail = messages[:4], messages[-4:]
    hidden = len(messages) - 8
    lines = [f"  ├─ [{_role_name(m)}] {_truncate(m.content)}" for m in head]
    lines.append(f"  │    … 中间 {hidden} 条折叠 …")
    lines += [f"  ├─ [{_role_name(m)}] {_truncate(m.content)}" for m in tail]
    lines[-1] = lines[-1].replace("├─", "└─", 1)
    return lines


def print_compression_report(before_msgs: list[BaseMessage],
                             removed: list[BaseMessage],
                             kept: list[BaseMessage],
                             old_summary: str,
                             new_summary: str,
                             before_tokens: int,
                             after_tokens: int,
                             trigger: str) -> None:
    """打印压缩前后的完整数据变化（仅在真正执行压缩时调用）。"""
    W = 66
    bar = "━" * W
    pct = (1 - after_tokens / before_tokens) * 100 if before_tokens else 0.0
    cnt_pct = (1 - len(kept) / len(before_msgs)) * 100 if before_msgs else 0.0

    print(f"\n{bar}")
    print(f"  对话摘要压缩报告（触发条件：{trigger}）")
    print(bar)

    print(f"\n[压缩前] {len(before_msgs)} 条消息 · 估算 {before_tokens} tokens")
    print(f"  旧摘要：{_truncate(old_summary, 50) if old_summary else '（无）'}")
    print("  消息列表：")
    print("\n".join(_msg_lines(before_msgs)))

    print(f"\n[压缩动作]")
    print(f"  · LLM 增量摘要：旧摘要 + {len(removed)} 条消息 → 新摘要（{settings.llm_model}）")
    print(f"  · RemoveMessage 删除 {len(removed)} 条（add_messages 按 id 合并，必须显式删除）")
    print(f"  · 保留最近 {len(kept)} 条原文进 prompt")

    print(f"\n[压缩后] {len(kept)} 条消息 + 摘要 {len(new_summary)} 字 · 估算 {after_tokens} tokens")
    print(f"  新摘要：{new_summary}")

    print(f"\n[效果]")
    print(f"  token   {before_tokens:>6} → {after_tokens:<6} ↓ {pct:.1f}%")
    print(f"  条数     {len(before_msgs):>6} → {len(kept):<6} ↓ {cnt_pct:.1f}%")
    print(bar + "\n")


def print_skip_report(messages: list[BaseMessage], tokens: int, reason: str) -> None:
    """未压缩时的简短说明（兜底或阈值边缘情况）。"""
    print(f"\n[压缩跳过] {len(messages)} 条消息 · 估算 {tokens} tokens · 原因：{reason}\n")


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """粗略 token 估算：中文约 1 token/字，英文约 1 token/4 字符。"""
    total = 0
    for m in messages:
        text = str(m.content)
        cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
        other = len(text) - cjk
        total += cjk + other // 4 + 4  # +4 每条消息的角色等开销
    return total


def _format_messages(messages: list[BaseMessage]) -> str:
    lines = []
    for m in messages:
        role = {HumanMessage: "用户", AIMessage: "助手", SystemMessage: "系统"}.get(
            type(m), getattr(m, "role", "消息"))
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


async def _summarize(old_summary: str, to_compress: list[BaseMessage]) -> str | None:
    """增量摘要：把旧摘要 + 新消息喂 LLM，生成融合后的新摘要。失败返回 None。"""
    if llm_client.is_mock or not await llm_client.is_available():
        return None
    parts = []
    if old_summary:
        parts.append(f"已有摘要：{old_summary}")
    parts.append(f"新增对话：\n{_format_messages(to_compress)}")
    try:
        text = await llm_client.chat(
            _SUMMARY_SYSTEM,
            "\n\n".join(parts) + "\n\n请输出融合后的完整摘要（覆盖旧摘要内容）：",
            temperature=0.1, max_tokens=300)
        return text if text and not text.startswith("[mock-llm]") else None
    except Exception as e:  # noqa: BLE001
        logger.warning("摘要生成失败，放弃本次压缩：%s", e)
        return None


async def maybe_compress_history(state: ResearchState) -> dict:
    """条件压缩节点：messages 未超阈值时零成本返回；超阈值才调 LLM。"""
    messages: list[BaseMessage] = list(state.get("messages") or [])
    tokens = estimate_tokens(messages)
    if len(messages) <= MAX_MESSAGES and tokens <= MAX_TOKENS:
        return {}

    keep = messages[-KEEP_RECENT:] if KEEP_RECENT else []
    to_compress = messages[:len(messages) - len(keep)] if KEEP_RECENT else messages
    if not to_compress:
        return {}
    trigger = (f"{len(messages)} 条 > {MAX_MESSAGES} 条阈值"
               if len(messages) > MAX_MESSAGES else f"{tokens} tokens > {MAX_TOKENS} 阈值")

    old_summary = state.get("conversation_summary", "")
    new_summary = await _summarize(old_summary, to_compress)
    if not new_summary:
        # 兜底：LLM 不可用 → 不压缩（宁可 prompt 长，不可丢历史）
        logger.warning("摘要不可得，跳过压缩（messages=%d, 估算 %d tokens）",
                       len(messages), tokens)
        print_skip_report(messages, tokens, "LLM 不可达，保留原图执行")
        return {}

    removals = [RemoveMessage(id=m.id) for m in to_compress if m.id]
    logger.info("对话压缩：%d 条 → 1 条摘要 + 最近 %d 条原文", len(to_compress), len(keep))
    print_compression_report(
        before_msgs=messages, removed=to_compress, kept=keep,
        old_summary=old_summary, new_summary=new_summary,
        before_tokens=tokens,
        after_tokens=estimate_tokens(keep) + _disp_width(new_summary),
        trigger=trigger)
    return {"conversation_summary": new_summary, "messages": removals}


def build_chat_context(state: ResearchState, max_recent: int = KEEP_RECENT) -> list[BaseMessage]:
    """喂 LLM 时组装上下文：[摘要 SystemMessage] + 最近 N 条原文。

    任何需要读对话历史的节点/工具都应通过本函数取上下文，
    而不是直接用 state["messages"]（那可能已是被压缩后的残集）。
    """
    ctx: list[BaseMessage] = []
    summary = state.get("conversation_summary", "")
    if summary:
        ctx.append(SystemMessage(content=f"历史对话摘要：{summary}"))
    ctx.extend((state.get("messages") or [])[-max_recent:])
    return ctx
