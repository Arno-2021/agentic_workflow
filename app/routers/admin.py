# -*- coding: utf-8 -*-
"""管理路由：数据看板 / 工具清单 / 跟踪列表 / 审计日志。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import store
from ..security import current_user
from ..tools import tool_inventory

router = APIRouter(prefix="/api", tags=["管理"])


@router.get("/dashboard")
async def dashboard(user: dict = Depends(current_user)):
    """基金研究数据看板（文档 4.1 投委会端）。"""
    return store.dashboard(user["department_id"])


@router.get("/tools")
async def tools(user: dict = Depends(current_user)):
    """工具调用清单（LLM 6 / 规则引擎 / MCP 4 / 外部 API 封装）。"""
    return {"tools": tool_inventory(), "total": len(tool_inventory())}


@router.get("/tracking")
async def tracking(user: dict = Depends(current_user)):
    """单只基金跟踪列表（长期记忆）。"""
    return {"tracking": store.list_tracking(user["department_id"], user["user_id"])}


@router.get("/audit-logs")
async def audit_logs(user: dict = Depends(current_user)):
    """审计日志（留痕 5 年备查）。"""
    return {"logs": store.list_logs(user["department_id"])}


@router.get("/history/{thread_id}")
async def history(thread_id: str, user: dict = Depends(current_user)):
    """对话历史（短期记忆，Checkpointer 之外的消息落库）。"""
    return {"messages": store.list_messages(thread_id)}


@router.get("/macro")
async def macro(user: dict = Depends(current_user)):
    """宏观数据（央行统计数据，mock）。"""
    from .. import mock_data
    return mock_data.MACRO_DATA
