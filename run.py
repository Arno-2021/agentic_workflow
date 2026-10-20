# -*- coding: utf-8 -*-
"""
FundAgent AI 一键启动
====================
用法：
    python run.py                # 启动 Web 服务（默认 127.0.0.1:8000）
    python run.py --port 9000    # 指定端口
    python run.py --demo         # 不启服务，直接跑 CLI 演示

LLM 模式：
    默认 mock（离线模板，开箱即跑）；
    real 模式设置环境变量：
        export FUNDAGENT_LLM_MODE=real
        export FUNDAGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
        export FUNDAGENT_LLM_API_KEY=sk-xxx
        export FUNDAGENT_LLM_MODEL=qwen-plus
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="FundAgent AI 智能基金研究 Agent 系统")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--demo", action="store_true", help="运行 CLI 演示（三个文档示例场景）")
    args = parser.parse_args()

    from app.config import settings

    if args.demo:
        import asyncio
        from scripts.demo import run_demo
        asyncio.run(run_demo())
        return

    import uvicorn
    host = args.host or settings.host
    port = args.port or settings.port
    print("=" * 62)
    print("  FundAgent AI · 智能基金研究 Agent 系统")
    print(f"  LLM 模式：{settings.llm_mode}（mock=离线模板 / real=OpenAI 兼容接口）")
    print(f"  访问地址：http://{host}:{port}")
    print("  演示账号：analyst_001 / fm_001 / ic_001，密码均为 123456")
    print("=" * 62)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
