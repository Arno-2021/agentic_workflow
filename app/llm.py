# -*- coding: utf-8 -*-
"""
LLM 客户端（默认对接本机 Ollama qwen3.5:9b）
============================================
 Ollama 适配要点（实测结论，Ollama 0.40.3）：
 1. qwen3.5 是思考模型。OpenAI 兼容端点 /v1/chat/completions 会**忽略** think=false，
    导致 token 全烧在 reasoning、content 返回空（finish_reason=length）。
 2. 原生端点 /api/chat 正确支持 think=false（content 直出、thinking 为 null），
    且支持 format="json" 做语法约束输出。
 3. 因此：Ollama 一律走原生端点；其他 OpenAI 兼容服务（DashScope/vLLM 等）
    走 /v1/chat/completions + response_format=json_object。

 降级：real 模式下 LLM 不可达/解析失败，工具层自动回退模板实现（打警告），
 保证 LangGraph 流程不中断（文档 3.4.1 ④ 三级降级）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger("fundagent.llm")

# 可用性探测缓存（秒）
_AVAIL_TTL = 60.0
_avail_cache: tuple[float, bool] = (0.0, False)


class LLMClient:
    """统一的 LLM 调用入口。"""

    def __init__(self) -> None:
        self.mode = settings.llm_mode

    # ------------------------------------------------------------------
    @property
    def is_mock(self) -> bool:
        return self.mode != "real"

    @property
    def _is_ollama(self) -> bool:
        return "11434" in settings.llm_base_url or "ollama" in settings.llm_base_url.lower()

    def _native_base(self) -> str:
        """Ollama 原生 API 基址（剥掉 /v1 后缀）。"""
        return settings.llm_base_url.rstrip("/").removesuffix("/v1")

    # ------------------------------------------------------------------
    async def is_available(self) -> bool:
        """探测 LLM 服务是否可达（带 60s 缓存，避免每个节点都探测）。"""
        global _avail_cache
        now = time.monotonic()
        cached_at, cached_val = _avail_cache
        if now - cached_at < _AVAIL_TTL:
            return cached_val
        ok = False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if self._is_ollama:
                    resp = await client.get(self._native_base() + "/api/tags")
                else:
                    resp = await client.get(
                        settings.llm_base_url.rstrip("/") + "/models",
                        headers={"Authorization": f"Bearer {settings.llm_api_key}"})
                ok = resp.status_code == 200
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 服务探测失败：%s", e)
        _avail_cache = (now, ok)
        return ok

    # ------------------------------------------------------------------
    async def chat(self, system: str, user: str, temperature: float = 0.3,
                   max_tokens: int | None = None) -> str:
        """通用对话（返回 content）。"""
        if self.is_mock:
            return f"[mock-llm] {user[:120]}"
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        if self._is_ollama:
            content = await self._ollama_chat(messages, temperature, max_tokens, json_mode=False)
        else:
            content = await self._openai_chat(messages, temperature, max_tokens, json_mode=False)
        if not content:
            raise RuntimeError("LLM 返回空 content")
        return content

    # ------------------------------------------------------------------
    async def chat_json(self, system: str, user: str,
                        max_tokens: int | None = None) -> dict[str, Any]:
        """要求 LLM 输出 JSON。解析失败自动带错误信息重试一次。"""
        if self.is_mock:
            return {}
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user + "\n\n只输出 JSON，不要输出任何其他文字。"}]
        raw = ""
        try:
            if self._is_ollama:
                raw = await self._ollama_chat(messages, 0.1, max_tokens, json_mode=True)
            else:
                raw = await self._openai_chat(messages, 0.1, max_tokens, json_mode=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM JSON 调用失败：%s", e)
            return {}
        parsed = _parse_json(raw)
        if parsed is not None:
            return parsed
        # 重试一次：把非法输出回喂给模型修正
        try:
            fixed = await self.chat(
                system, f"下面这段内容不是合法 JSON，请修正后只输出 JSON：\n{raw[:2000]}",
                temperature=0.0, max_tokens=max_tokens)
            parsed = _parse_json(fixed)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM JSON 重试失败：%s", e)
        return parsed or {}

    # ------------------------------------------------------------------
    # Ollama 原生端点
    # ------------------------------------------------------------------
    async def _ollama_chat(self, messages: list[dict], temperature: float,
                           max_tokens: int | None, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "stream": False,
            # 默认关闭思考：qwen3.5 思考模式 content 为空、且显著变慢（实测）
            # FUNDAGENT_LLM_THINK=true 可开启（自行承担 content 为空/降级模板的后果）
            "think": settings.llm_think,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(self._native_base() + "/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return (data.get("message", {}).get("content") or "").strip()

    # ------------------------------------------------------------------
    # OpenAI 兼容端点（DashScope / DeepSeek / vLLM 等）
    # ------------------------------------------------------------------
    async def _openai_chat(self, messages: list[dict], temperature: float,
                           max_tokens: int | None, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            resp.raise_for_status()
            data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


def _parse_json(raw: str) -> dict | None:
    """从模型输出中稳健提取 JSON 对象（剥离 markdown 围栏/前后缀噪声）。"""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


llm_client = LLMClient()
