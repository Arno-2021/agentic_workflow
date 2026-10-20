# -*- coding: utf-8 -*-
"""
研究任务驱动服务
================
把 LangGraph 图的执行、HitL 中断恢复、SSE 事件推送、持久化封装成服务层，
供 FastAPI 路由调用（对应文档 5.1 数据流转：意图识别 → Pipeline → SSE 输出 → 记忆保存）。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .db import store
from .graph import fund_agent_graph
from .graph.events import event_bus


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _new_thread_id() -> str:
    return f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class ResearchService:
    """任务生命周期：创建 → 执行（可能中断）→ 审核恢复 → 完成。"""

    # ------------------------------------------------------------------
    async def run_task(self, department_id: str, user_id: str, query: str,
                       thread_id: str | None = None) -> str:
        """启动一个基金研究任务（后台执行 LangGraph 图）。"""
        thread_id = thread_id or _new_thread_id()
        store.create_session(thread_id, department_id, user_id, title=query[:30])
        store.save_message(thread_id, department_id, user_id, "user", query)
        store.log("task_started", department_id, user_id, {"thread_id": thread_id, "query": query})
        event_bus.emit(thread_id, {"type": "status", "message": "任务已启动，Orchestrator 意图识别中…"})
        asyncio.create_task(self._execute(thread_id, department_id, user_id, query))
        return thread_id

    async def _execute(self, thread_id: str, department_id: str, user_id: str, query: str) -> None:
        try:
            initial = {
                "department_id": department_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "query": query,
                "messages": [HumanMessage(content=query)],
            }
            result = await fund_agent_graph.ainvoke(initial, config=_config(thread_id))
            state = fund_agent_graph.get_state(_config(thread_id))
            if state.next:  # human_review interrupt
                store.touch_session(thread_id, status="awaiting_review")
                event_bus.emit(thread_id, {"type": "paused",
                                           "message": "分析结论待基金经理确认（Human-in-the-Loop）"})
            else:
                self._finish(thread_id, department_id, user_id, result)
        except Exception as e:  # noqa: BLE001
            event_bus.emit(thread_id, {"type": "error", "message": f"任务执行失败：{e}"})
            store.log("task_failed", department_id, user_id, {"thread_id": thread_id, "error": str(e)})

    # ------------------------------------------------------------------
    async def resume_task(self, department_id: str, user_id: str, thread_id: str,
                          decision: str, comments: str = "") -> None:
        """基金经理审核：确认 → 继续保存；退回 → 带意见重新生成。"""
        store.save_message(thread_id, department_id, user_id, "user",
                           f"【审核】{decision} {comments}".strip())
        store.log("review_submitted", department_id, user_id,
                  {"thread_id": thread_id, "decision": decision, "comments": comments})
        event_bus.emit(thread_id, {"type": "status",
                                   "message": f"已收到审核决定：{decision}"})
        asyncio.create_task(self._resume(thread_id, department_id, user_id, decision, comments))

    async def _resume(self, thread_id: str, department_id: str, user_id: str,
                      decision: str, comments: str) -> None:
        try:
            result = await fund_agent_graph.ainvoke(
                Command(resume={"decision": decision, "comments": comments}),
                config=_config(thread_id))
            state = fund_agent_graph.get_state(_config(thread_id))
            if state.next:  # 退回后再次进入 human_review
                store.touch_session(thread_id, status="awaiting_review")
                event_bus.emit(thread_id, {"type": "paused",
                                           "message": "已按意见重新生成，等待再次确认"})
            else:
                self._finish(thread_id, department_id, user_id, result)
        except Exception as e:  # noqa: BLE001
            event_bus.emit(thread_id, {"type": "error", "message": f"审核恢复失败：{e}"})

    # ------------------------------------------------------------------
    def _finish(self, thread_id: str, department_id: str, user_id: str, result: dict) -> None:
        answer = result.get("final_answer", "（无输出）")
        store.save_message(thread_id, department_id, user_id, "assistant", answer)
        store.touch_session(thread_id, status="completed")
        store.log("task_completed", department_id, user_id, {"thread_id": thread_id})
        event_bus.emit(thread_id, {"type": "final", "message": answer,
                                   "thread_id": thread_id,
                                   "report_id": result.get("report_id")})

    # ------------------------------------------------------------------
    def get_status(self, thread_id: str) -> dict:
        """查询任务状态（含 HitL 暂停时的待审信息）。"""
        state = fund_agent_graph.get_state(_config(thread_id))
        values = state.values or {}
        return {
            "thread_id": thread_id,
            "paused": bool(state.next),
            "next_nodes": list(state.next),
            "stage": values.get("stage", ""),
            "intent": values.get("intent", ""),
            "plan": values.get("plan", []),
            "agents_done": values.get("agents_done", []),
            "progress": values.get("progress", []),
            "review_decision": values.get("review_decision", ""),
            "report_id": values.get("report_id", ""),
        }


research_service = ResearchService()
