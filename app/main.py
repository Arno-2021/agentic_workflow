# -*- coding: utf-8 -*-
"""FundAgent AI - FastAPI 应用入口。"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import mock_data
from .config import settings
from .db import init_db, store
from .routers import admin, auth, chat, reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx 的请求日志太吵，压到 WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    init_db()
    # 初始化研究员偏好（长期记忆种子数据）
    for u in mock_data.USERS:
        if u.get("preferences"):
            store.upsert_preferences(u["department_id"], u["user_id"], u["preferences"])

    app = FastAPI(title="FundAgent AI 智能基金研究 Agent 系统",
                  description="LangGraph 多 Agent（DataAgent/AnalysisAgent/ReportAgent）+ "
                              "Orchestrator 编排，SSE 流式输出，Human-in-the-Loop 审核",
                  version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(reports.router)
    app.include_router(admin.router)

    app.mount("/", __import__("fastapi").staticfiles.StaticFiles(
        directory=str(__import__("pathlib").Path(__file__).parent / "static"),
        html=True), name="static")
    return app


app = create_app()
