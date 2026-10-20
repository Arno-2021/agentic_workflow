# -*- coding: utf-8 -*-
"""
冒烟自检脚本
============
验证当前环境是否满足 FundAgent AI 运行要求，逐项检查：
  1. 依赖导入（langgraph / fastapi / jwt）
  2. SQLite Checkpointer（中断暂停 / Command 恢复 / 状态持久化）
  3. 顶层图组装（三 Agent 子图 + Orchestrator）
  4. mock 数据完整性

用法：python tests/probe_cp.py
全部通过会打印 SMOKE OK；任一失败以非零码退出。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Annotated, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app import mock_data
from app.db import init_db, store
from app.graph import fund_agent_graph, classify_intent, build_plan
from app.graph.checkpointer import checkpointer


class S(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    x: int


def n1(state: S):
    return {"x": state.get("x", 0) + 1}


def n2(state: S):
    ans = interrupt({"question": "确认吗?"})
    return {"x": state.get("x", 0) + (1 if ans.get("ok") else 0)}


async def main() -> None:
    # 1. 依赖
    import fastapi, jwt, langgraph  # noqa: F401
    print("PASS 1/4 依赖导入（langgraph/fastapi/pyjwt）")

    # 2. Checkpointer：中断 → 恢复 → 多轮
    init_db()
    g = StateGraph(S)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.add_edge(START, "n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    app = g.compile(checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": "smoke_t1"}}
    await app.ainvoke({"messages": [], "x": 0}, config=cfg)
    st = app.get_state(cfg)
    assert st.next == ("n2",), st.next
    await app.ainvoke(Command(resume={"ok": True}), config=cfg)
    assert app.get_state(cfg).values.get("x") == 2
    print("PASS 2/4 SQLite Checkpointer（interrupt 暂停 / Command 恢复 / 状态持久化）")

    # 3. 顶层图 + 意图路由
    assert fund_agent_graph is not None
    assert classify_intent("查一下贵州茅台最新的财务数据") == "data_query"
    assert classify_intent("帮我分析一下宁德时代的投资价值") == "stock_analysis"
    assert "report_agent" in build_plan("stock_analysis", "帮我分析宁德时代并生成基金研究报告")
    print("PASS 3/4 顶层图组装 + 8 类意图路由")

    # 4. mock 数据
    assert len(mock_data.STOCKS) == 6
    assert mock_data.find_stock("宁德时代")["stock_code"] == "300750.SZ"
    assert store.dashboard("research_dept_1") is not None
    print("PASS 4/4 mock 数据与持久化层")

    print("\nSMOKE OK —— 环境就绪，可以运行：python run.py 或 python run.py --demo")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print(f"\nSMOKE FAILED: {e}")
        sys.exit(1)
