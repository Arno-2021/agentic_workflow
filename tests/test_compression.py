# -*- coding: utf-8 -*-
"""
摘要压缩方案测试
===============
覆盖：阈值内零成本、超阈值压缩（RemoveMessage 删除）、增量摘要、
LLM 不可达兜底、token 估算、顶层图集成。

运行：python tests/test_compression.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage

from app.graph import fund_agent_graph
from app.graph.state import ResearchState
from app.graph.summarize import (
    KEEP_RECENT,
    MAX_MESSAGES,
    _disp_width,
    estimate_tokens,
    maybe_compress_history,
)


def make_state(n_msgs: int, summary: str = "") -> ResearchState:
    msgs = []
    for i in range(n_msgs):
        if i % 2 == 0:
            msgs.append(HumanMessage(content=f"第{i//2}轮：帮我分析宁德时代第{i//2}号问题", id=f"h{i}"))
        else:
            msgs.append(AIMessage(content=f"第{i//2}轮答复：估值360元，评级买入（编号{i}）", id=f"a{i}"))
    return {"messages": msgs, "conversation_summary": summary}


async def main() -> None:
    # 1. 阈值内：零成本不压缩
    st = make_state(MAX_MESSAGES)  # 恰好等于阈值
    out = await maybe_compress_history(st)
    assert out == {}, f"阈值内不应压缩: {out}"
    print(f"PASS 1/6 阈值内零成本（{MAX_MESSAGES} 条不触发）")

    # 2. 超阈值：压缩为 摘要 + 最近 KEEP_RECENT 条
    st = make_state(MAX_MESSAGES + 6)
    before = estimate_tokens(st["messages"])
    out = await maybe_compress_history(st)
    if os.getenv("FUNDAGENT_LLM_MODE", "real") == "real":
        assert out.get("conversation_summary"), "应生成摘要"
        removed = [m for m in out["messages"] if type(m).__name__ == "RemoveMessage"]
        assert len(removed) == MAX_MESSAGES + 6 - KEEP_RECENT, f"删除数不对: {len(removed)}"
        assert "摘要" in out["conversation_summary"] or len(out["conversation_summary"]) > 10
        # 与报告同一估算口径：原文 token + 摘要显示宽度
        after_tokens = estimate_tokens(st["messages"][-KEEP_RECENT:]) + _disp_width(
            out["conversation_summary"])
        print(f"PASS 2/6 超阈值压缩：{MAX_MESSAGES + 6} 条 → 摘要 + 最近 {KEEP_RECENT} 条"
              f"（估算 token {before} → {after_tokens}，↓{(1 - after_tokens / before) * 100:.0f}%）")
    else:
        print("SKIP 2/6 mock 模式跳过（LLM 不可用时应走兜底，见 4）")

    # 3. 增量摘要：在已有摘要基础上二次压缩
    if os.getenv("FUNDAGENT_LLM_MODE", "real") == "real":
        st2 = make_state(MAX_MESSAGES + 6, summary="用户此前分析过比亚迪，评级增持。")
        out2 = await maybe_compress_history(st2)
        assert out2.get("conversation_summary"), "增量摘要应生成"
        print(f"PASS 3/6 增量摘要：新摘要融合了旧摘要（{len(out2['conversation_summary'])} 字）")

    # 4. 兜底：mock 模式（LLM 不可用）→ 不压缩
    os.environ["FUNDAGENT_LLM_MODE"] = "mock"
    from app.llm import llm_client
    llm_client.mode = "mock"
    st3 = make_state(MAX_MESSAGES + 10)
    out3 = await maybe_compress_history(st3)
    assert out3 == {}, f"LLM 不可用不应压缩: {out3}"
    print("PASS 4/6 兜底：LLM 不可达时不压缩、不丢历史")
    llm_client.mode = "real"
    os.environ["FUNDAGENT_LLM_MODE"] = "real"

    # 5. token 估算合理性（200 个中文字符 ≈ 204 tokens）
    msgs = [HumanMessage(content="你好" * 100)]
    est = estimate_tokens(msgs)
    assert 180 <= est <= 240, f"估算异常: {est}"
    print(f"PASS 5/6 token 估算：200 中文字符 ≈ {est} tokens")

    # 6. 顶层图集成：压缩节点已插入且链路正常
    nodes = set(fund_agent_graph.get_graph().nodes)
    assert "maybe_compress_history" in nodes, "压缩节点未入图"
    assert "orchestrator" in nodes and "data_agent" in nodes
    print("PASS 6/6 顶层图集成：START → maybe_compress_history → orchestrator")

    print("\nCOMPRESSION OK —— 摘要压缩方案就绪")


if __name__ == "__main__":
    asyncio.run(main())
