# -*- coding: utf-8 -*-
"""对话路由：发起任务（SSE 流式）、任务状态、Human-in-the-Loop 审核。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..security import current_user, require_roles
from ..service import research_service

router = APIRouter(prefix="/api", tags=["对话"])


@router.post("/chat")
async def chat(payload: dict, user: dict = Depends(current_user)):
    """发起基金研究任务。返回 thread_id，前端通过 SSE 订阅执行进度。"""
    message = (payload.get("message") or "").strip()
    if not message:
        return {"error": "message 不能为空"}
    thread_id = await research_service.run_task(
        department_id=user["department_id"], user_id=user["user_id"],
        query=message, thread_id=payload.get("thread_id"))
    return {"thread_id": thread_id, "status": "running"}


@router.get("/chat/stream")
async def chat_stream(request: Request, thread_id: str = Query(...),
                      token: str = Query(...)):
    """SSE 流式输出任务进度（EventSource 无法自定义 Header，token 走 Query）。"""
    from ..security import decode_token
    decode_token(token)
    channel = None

    async def event_gen():
        nonlocal channel
        from ..graph.events import event_bus
        channel = event_bus.channel(thread_id)
        idx = 0
        while True:
            if await request.is_disconnected():
                return
            events = channel.snapshot()
            while idx < len(events):
                e = events[idx]
                idx += 1
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                if e.get("type") in ("final", "error"):
                    return
            await asyncio.sleep(0.15)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/tasks")
async def list_tasks(user: dict = Depends(current_user)):
    """历史任务列表（90 天内）。"""
    from ..db import store
    return {"sessions": store.list_sessions(user["department_id"], user["user_id"])}


@router.get("/tasks/{thread_id}")
async def task_status(thread_id: str, user: dict = Depends(current_user)):
    """任务状态（含 HitL 暂停时的待审信息与节点流水）。"""
    return research_service.get_status(thread_id)


@router.post("/tasks/{thread_id}/review")
async def review(thread_id: str, payload: dict,
                 user: dict = Depends(require_roles("fund_manager", "committee"))):
    """Human-in-the-Loop：基金经理确认 / 退回修改（恢复 LangGraph 断点）。"""
    decision = payload.get("decision", "approve")
    if decision not in ("approve", "reject"):
        return {"error": "decision 必须是 approve / reject"}
    await research_service.resume_task(
        department_id=user["department_id"], user_id=user["user_id"],
        thread_id=thread_id, decision=decision,
        comments=payload.get("comments", ""))
    return {"thread_id": thread_id, "status": "resuming"}


@router.post("/tasks/{thread_id}/cancel")
async def cancel_task(thread_id: str, user: dict = Depends(current_user)):
    """取消正在运行或暂停中的任务。"""
    result = await research_service.cancel_task(
        thread_id=thread_id,
        department_id=user["department_id"],
        user_id=user["user_id"])
    return result
