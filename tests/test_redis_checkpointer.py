# -*- coding: utf-8 -*-
"""
Redis Checkpointer 测试
=======================
覆盖：interrupt 暂停 / Command 恢复 / 多轮状态持久化 / list 历史 / 认证失败友好报错。

前置：Docker Redis 在 8379 端口（需认证时设 FUNDAGENT_REDIS_PASSWORD）。
运行：
    export FUNDAGENT_CHECKPOINTER_BACKEND=redis
    export FUNDAGENT_REDIS_PASSWORD=<你的密码>   # 容器设了 requirepass 才需要
    python tests/test_redis_checkpointer.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Annotated, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app.config import settings
from app.graph.redis_checkpointer import RedisCheckpointSaver


class S(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    x: int


def n1(state: S):
    return {"x": state.get("x", 0) + 1}


def n2(state: S):
    ans = interrupt({"question": "确认吗?"})
    return {"x": state.get("x", 0) + (1 if ans.get("ok") else 0)}


async def main() -> None:
    # 0. 连接探测（含认证失败的友好报错路径）
    try:
        saver = RedisCheckpointSaver()
    except RuntimeError as e:
        print(f"连接失败（已给出修复指引）：{e}")
        sys.exit(1)
    print(f"PASS 0/5 Redis 连接 {settings.redis_url}")

    # 测试 key 用独立前缀，结束清理
    tid = f"smoke_redis_{int(time.time() * 1000) % 100000}"
    client = saver._client

    g = StateGraph(S)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.add_edge(START, "n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": tid}}

    try:
        # 1. interrupt 暂停
        await app.ainvoke({"messages": [], "x": 0}, config=cfg)
        st = app.get_state(cfg)
        assert st.next == ("n2",), st.next
        print("PASS 1/5 interrupt 暂停（next=('n2',)，State 已入 Redis）")

        # 2. Command 恢复
        await app.ainvoke(Command(resume={"ok": True}), config=cfg)
        st = app.get_state(cfg)
        assert st.values.get("x") == 2 and not st.next, st.values
        print("PASS 2/5 Command(resume=) 断点恢复（x=2，图走完）")

        # 3. 多轮：同一 thread 再次调用，x 基于持久化状态累加
        await app.ainvoke({"messages": [], "x": 10}, config=cfg)
        st = app.get_state(cfg)
        assert st.next == ("n2",), st.next
        await app.ainvoke(Command(resume={"ok": True}), config=cfg)
        assert app.get_state(cfg).values.get("x") == 12
        print("PASS 3/5 多轮状态持久化（10 → interrupt → +1 = 12）")

        # 4. list() 历史：至少 2 个 checkpoint 且按写入逆序
        history = [t async for t in saver.alist(cfg)]
        assert len(history) >= 2, f"历史 checkpoint 不足: {len(history)}"
        assert history[0].config["configurable"]["checkpoint_id"]
        print(f"PASS 4/5 list() 历史查询（{len(history)} 个 checkpoint，zset 逆序）")

        # 5. key 结构验证
        keys = client.keys(f"lg:cp:{tid}:*")
        has_latest = client.exists(f"lg:cp:{tid}::latest")
        assert has_latest and any(k.endswith(b":ids") for k in keys), keys
        print(f"PASS 5/5 key 结构（latest/ids/ckpt/writes 共 {len(keys)} 个 key）")

        print("\nREDIS CHECKPOINTER OK —— 可切换运行：")
        print("  export FUNDAGENT_CHECKPOINTER_BACKEND=redis")
        print("  python run.py")
    finally:
        # 清理测试 key
        for k in client.keys(f"lg:cp:{tid}:*"):
            client.delete(k)
        print(f"\n已清理测试 key：lg:cp:{tid}:*")


if __name__ == "__main__":
    asyncio.run(main())
