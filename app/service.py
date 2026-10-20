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
from typing import Dict

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

    def __init__(self):
        # 保存运行中任务的引用，用于取消
        self._running_tasks: Dict[str, asyncio.Task] = {}
        # 取消标记，LangGraph 执行过程中检查
        self._cancelled_threads: set[str] = set()

    # ------------------------------------------------------------------
    async def run_task(self, department_id: str, user_id: str, query: str,
                       thread_id: str | None = None) -> str:
        """启动一个基金研究任务（后台执行 LangGraph 图）。"""
        thread_id = thread_id or _new_thread_id()
        store.create_session(thread_id, department_id, user_id, title=query[:30])
        store.save_message(thread_id, department_id, user_id, "user", query)
        store.log("task_started", department_id, user_id, {"thread_id": thread_id, "query": query})
        event_bus.emit(thread_id, {"type": "status", "message": "任务已启动，Orchestrator 意图识别中…"})

        # 保存任务引用，用于取消
        task = asyncio.create_task(self._execute(thread_id, department_id, user_id, query))
        self._running_tasks[thread_id] = task

        # 任务完成后自动清理
        def cleanup(_):
            self._running_tasks.pop(thread_id, None)
            self._cancelled_threads.discard(thread_id)
        task.add_done_callback(cleanup)

        return thread_id

    async def _execute(self, thread_id: str, department_id: str, user_id: str, query: str) -> None:
        try:
            # 检查是否已被取消
            if thread_id in self._cancelled_threads:
                store.touch_session(thread_id, status="cancelled")
                event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
                return

            initial = {
                "department_id": department_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "query": query,
                "messages": [HumanMessage(content=query)],
            }
            result = await fund_agent_graph.ainvoke(initial, config=_config(thread_id))

            # 执行完成后再次检查取消标记（防止执行期间被取消）
            if thread_id in self._cancelled_threads:
                store.touch_session(thread_id, status="cancelled")
                event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
                return

            state = fund_agent_graph.get_state(_config(thread_id))
            if state.next:  # human_review interrupt
                store.touch_session(thread_id, status="awaiting_review")
                event_bus.emit(thread_id, {"type": "paused",
                                           "message": "分析结论待基金经理确认（Human-in-the-Loop）"})
            else:
                self._finish(thread_id, department_id, user_id, result)
        except asyncio.CancelledError:
            # 任务被取消
            store.touch_session(thread_id, status="cancelled")
            event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
            store.log("task_cancelled", department_id, user_id, {"thread_id": thread_id})
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

        # 保存任务引用
        task = asyncio.create_task(self._resume(thread_id, department_id, user_id, decision, comments))
        self._running_tasks[thread_id] = task

        # 任务完成后自动清理
        def cleanup(_):
            self._running_tasks.pop(thread_id, None)
            self._cancelled_threads.discard(thread_id)
        task.add_done_callback(cleanup)

    async def _resume(self, thread_id: str, department_id: str, user_id: str,
                      decision: str, comments: str) -> None:
        try:
            # 检查是否已被取消
            if thread_id in self._cancelled_threads:
                store.touch_session(thread_id, status="cancelled")
                event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
                return

            result = await fund_agent_graph.ainvoke(
                Command(resume={"decision": decision, "comments": comments}),
                config=_config(thread_id))

            # 执行完成后再次检查取消标记
            if thread_id in self._cancelled_threads:
                store.touch_session(thread_id, status="cancelled")
                event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
                return

            state = fund_agent_graph.get_state(_config(thread_id))
            if state.next:  # 退回后再次进入 human_review
                store.touch_session(thread_id, status="awaiting_review")
                event_bus.emit(thread_id, {"type": "paused",
                                           "message": "已按意见重新生成，等待再次确认"})
            else:
                self._finish(thread_id, department_id, user_id, result)
        except asyncio.CancelledError:
            # 任务被取消
            store.touch_session(thread_id, status="cancelled")
            event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
            store.log("task_cancelled", department_id, user_id, {"thread_id": thread_id})
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
    async def cancel_task(self, thread_id: str, department_id: str, user_id: str) -> dict:
        """取消正在运行的任务。"""
        # 添加到取消标记集合
        self._cancelled_threads.add(thread_id)

        # 如果任务正在运行，尝试取消
        task = self._running_tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
            store.log("task_cancel_requested", department_id, user_id, {"thread_id": thread_id})
            event_bus.emit(thread_id, {"type": "status", "message": "正在取消任务..."})
            return {"status": "cancelling", "message": "任务正在取消"}

        # 任务已完成或不存在
        state = fund_agent_graph.get_state(_config(thread_id))
        if state.next:
            # 任务处于暂停状态（HitL），直接标记为取消
            store.touch_session(thread_id, status="cancelled")
            event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
            store.log("task_cancelled", department_id, user_id, {"thread_id": thread_id})
            return {"status": "cancelled", "message": "已取消暂停中的任务"}

        # 没有 LangGraph 状态，检查数据库
        from .dbutil import get_conn
        conn = get_conn()
        cursor = conn.execute(
            "SELECT status FROM chat_sessions WHERE thread_id = ? AND department_id = ?",
            (thread_id, department_id)
        )
        row = cursor.fetchone()

        if row:
            db_status = row[0]
            if db_status == "awaiting_review":
                # 数据库显示暂停（可能是测试场景或恢复的会话）
                store.touch_session(thread_id, status="cancelled")
                event_bus.emit(thread_id, {"type": "cancelled", "message": "任务已取消"})
                store.log("task_cancelled", department_id, user_id, {"thread_id": thread_id})
                return {"status": "cancelled", "message": "已取消暂停中的任务"}
            elif db_status in ["completed", "cancelled"]:
                return {"status": db_status, "message": f"任务已{db_status}"}

        return {"status": "not_running", "message": "任务未在运行或已完成"}

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
