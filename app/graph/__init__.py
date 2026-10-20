# -*- coding: utf-8 -*-
"""LangGraph 层导出：编译后的 FundAgent AI 顶层图（Checkpointer 后端可切换）。

- 默认 sqlite：零外部依赖，单文件（graph/checkpointer.py）
- redis：Docker 部署（默认 8379 端口），切换方式：
      export FUNDAGENT_CHECKPOINTER_BACKEND=redis
      export FUNDAGENT_REDIS_URL=redis://localhost:8379/0
      export FUNDAGENT_REDIS_PASSWORD=<你的密码>   # 容器设了 requirepass 才需要
"""
from __future__ import annotations

from .checkpointer import SqliteCheckpointSaver
from ..config import settings
from .orchestrator import build_fund_agent_graph, classify_intent, build_plan


def _make_checkpointer():
    if settings.checkpointer_backend == "redis":
        from .redis_checkpointer import make_redis_checkpointer
        print(f"[FundAgent] Checkpointer 后端：Redis（{settings.redis_url}）")
        return make_redis_checkpointer()
    print("[FundAgent] Checkpointer 后端：SQLite（data/fundagent.db）")
    return SqliteCheckpointSaver()


# 顶层图绑定 Checkpointer：按 thread_id 隔离 State，支持多轮记忆与 HitL 断点续传
checkpointer = _make_checkpointer()
fund_agent_graph = build_fund_agent_graph().compile(checkpointer=checkpointer)

__all__ = ["fund_agent_graph", "checkpointer", "classify_intent", "build_plan"]
