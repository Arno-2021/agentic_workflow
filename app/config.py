# -*- coding: utf-8 -*-
"""全局配置：LLM 模式（real/mock）、数据库路径、JWT 等。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "fundagent.db"


class Settings:
    # LLM 模式：
    #   real —— 调用 OpenAI 兼容接口（默认指向本机 Ollama，见下）
    #   mock —— 离线模板生成（Ollama 不可用时的降级兜底）
    # 自动降级：real 模式下若 LLM 服务不可达，工具层自动回退模板并打警告日志，
    # 保证图不中断（对应文档 3.4.1 ④ 三级降级策略）。
    llm_mode: str = os.getenv("FUNDAGENT_LLM_MODE", "real").lower()

    # 默认对接本机 Ollama 的 OpenAI 兼容端点（无需 Key，占位即可）
    llm_base_url: str = os.getenv("FUNDAGENT_LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = os.getenv("FUNDAGENT_LLM_API_KEY", "ollama")
    llm_model: str = os.getenv("FUNDAGENT_LLM_MODEL", "qwen3.5:9b")
    llm_timeout: float = float(os.getenv("FUNDAGENT_LLM_TIMEOUT", "180"))

    # qwen3.5 系列为思考模型：关闭 thinking，否则 token 全耗在 reasoning 上、content 为空
    # （仅对 Ollama 端点生效，其他 OpenAI 兼容服务会忽略该字段）
    llm_think: bool = os.getenv("FUNDAGENT_LLM_THINK", "false").lower() in ("1", "true", "yes")

    # JWT
    jwt_secret: str = os.getenv("FUNDAGENT_JWT_SECRET",
                                "fundagent-ai-dev-secret-2026-please-change-in-prod")
    jwt_ttl_seconds: int = int(os.getenv("FUNDAGENT_JWT_TTL", "7200"))

    # 服务
    host: str = os.getenv("FUNDAGENT_HOST", "127.0.0.1")
    port: int = int(os.getenv("FUNDAGENT_PORT", "8000"))

    # 一致性校验阈值（文档 Q2：差异 < 1% 取权威源）
    consistency_threshold: float = 0.01
    # 风险阈值（文档：估值偏离度 > 30% 标记高风险）
    risk_deviation_threshold: float = 0.30

    # Checkpointer 后端：sqlite（默认，零依赖）/ redis（Docker 部署）
    checkpointer_backend: str = os.getenv("FUNDAGENT_CHECKPOINTER_BACKEND", "sqlite")
    redis_url: str = os.getenv("FUNDAGENT_REDIS_URL", "redis://localhost:8379/0")
    redis_password: str = os.getenv("FUNDAGENT_REDIS_PASSWORD", "")
    # checkpoint TTL（天）：0=不过期；生产建议 90（文档：State 保留 90 天）
    checkpoint_ttl_days: int = int(os.getenv("FUNDAGENT_CHECKPOINTER_TTL_DAYS", "0"))


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
