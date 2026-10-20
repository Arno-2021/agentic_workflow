# -*- coding: utf-8 -*-
"""
CLI 演示：对应文档第四章三个示例场景
====================================
  示例一：数据查询（简单任务）      "查一下贵州茅台最新的财务数据"
  示例二：单只基金深度分析（复杂）  "帮我分析一下宁德时代的投资价值"（含 Human-in-the-Loop）
  示例三：跨 Agent 全流程          "帮我分析一下新能源主题…从数据采集到生成基金报告全流程处理"

运行：python scripts/demo.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from app.db import init_db, store
from app.graph import fund_agent_graph
from app.graph.events import event_bus

DEPT, USER = "research_dept_1", "analyst_001"


def print_flow(thread_id: str, seen: int) -> int:
    events = event_bus.snapshot(thread_id)
    for e in events[seen:]:
        if e["type"] == "progress":
            agent = e.get("agent", "")
            node = e.get("node", "")
            print(f"    [{agent:>11}] {node:<26} {e.get('message', '')}")
    return len(events)


async def run_case(title: str, query: str, need_review: bool) -> None:
    print("\n" + "=" * 70)
    print(f"🎬 {title}")
    print(f"💬 用户输入：{query}")
    print("-" * 70)
    thread_id = f"demo_{int(time.time() * 1000) % 100000}_{title[:4]}"
    seen = 0
    config = {"configurable": {"thread_id": thread_id}}

    await fund_agent_graph.ainvoke(
        {"department_id": DEPT, "user_id": USER, "thread_id": thread_id,
         "query": query, "messages": []}, config=config)
    seen = print_flow(thread_id, seen)

    state = fund_agent_graph.get_state(config)
    if state.next:  # Human-in-the-Loop 中断
        print("\n⏸  Human-in-the-Loop：分析结论已生成，等待基金经理确认…")
        print("   （生产环境：基金经理在 Web 界面点「确认/退回修改」，LangGraph 从断点恢复）")
        await fund_agent_graph.ainvoke(
            Command(resume={"decision": "approve", "comments": ""}), config=config)
        seen = print_flow(thread_id, seen)
        state = fund_agent_graph.get_state(config)

    result = state.values
    print("\n✅ 最终答复：")
    print(result.get("final_answer", "（无输出）"))
    if result.get("report_id"):
        print(f"\n📝 报告号：{result['report_id']}（已存基金报告库并推送基金经理）")


async def run_demo() -> None:
    init_db()
    print("FundAgent AI CLI 演示（mock 数据，6 只标的：宁德时代/比亚迪/贵州茅台/隆基绿能/阳光电源/亿纬锂能）")
    await run_case("示例一 数据查询", "查一下贵州茅台最新的财务数据", False)
    await run_case("示例二 单只基金深度分析", "帮我分析一下宁德时代的投资价值", True)
    await run_case("示例三 跨 Agent 全流程",
                   "帮我分析一下新能源主题的投资机会，从数据采集到生成基金报告全流程处理", True)

    print("\n" + "=" * 70)
    print("📌 演示结束。Web 界面体验：python run.py  →  http://127.0.0.1:8000")
    print("📌 生成的报告已落入 SQLite（data/fundagent.db），可用 /api/reports 查询")


if __name__ == "__main__":
    asyncio.run(run_demo())
