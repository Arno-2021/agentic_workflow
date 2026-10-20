# -*- coding: utf-8 -*-
"""
Orchestrator 编排器 + 顶层 LangGraph 图
========================================
职责（文档 3.3）：
  1. 意图识别：8 类基金研究意图（market_analysis / research_query / stock_analysis /
     portfolio_advice / data_query / macro_analysis / industry_compare / financial_diagnosis）
  2. 路由决策：单 Agent 直达 or 多 Agent 串联 Pipeline
  3. 上下文传递：三个 Agent 共享同一份 ResearchState（子图即节点）
  4. 汇总输出：summarize 节点生成最终答复

图结构：
  orchestrator ─conditional→ data_agent ─conditional→ analysis_agent ─conditional→ report_agent
                                    ↘ summarize（单 Agent 直达时跳过下游）
"""
from __future__ import annotations

import re
from typing import Literal

from langgraph.graph import END, START, StateGraph

from .analysis_agent import build_analysis_agent
from .data_agent import build_data_agent
from .events import emit_node_progress
from .report_agent import build_report_agent
from .state import ResearchState
from .summarize import maybe_compress_history

# ---------------------------------------------------------------------------
# 意图分类（文档 3.5 意图分类详表）
# ---------------------------------------------------------------------------

INTENT_PATTERNS: dict[str, list[str]] = {
    "market_analysis": ["走势", "行情怎么样", "主题", "板块", "速览"],
    "research_query": ["研报", "报告查询", "找一下", "深度报告", "有哪些.*报告", "检索"],
    "stock_analysis": ["投资价值", "值不值得", "深度分析", "分析一下", "买入.*建议"],
    "portfolio_advice": ["组合", "持仓", "优化", "配置建议"],
    "data_query": ["查一下", "查下", "财务数据", "公告", "最新数据"],
    "macro_analysis": ["宏观", "CPI", "M2", "GDP", "美联储", "货币政策", "降息", "加息", "LPR", "债市"],
    "industry_compare": ["对比", "比较", "哪个更有前景", "横向"],
    "financial_diagnosis": ["诊断", "财务健康", "现金流怎么样", "财务状况", "负债"],
    "report_generate": ["生成.*报告", "撰写.*报告", "写一份报告", "写一份研报"],
}

# 同分时的优先级（越具体越优先）
INTENT_PRIORITY = ["report_generate", "stock_analysis", "industry_compare", "macro_analysis",
                   "financial_diagnosis", "portfolio_advice", "market_analysis",
                   "research_query", "data_query"]

AGENT_ORDER = {"data_agent": 0, "analysis_agent": 1, "report_agent": 2}


def classify_intent(query: str) -> str:
    """关键词加权意图分类（mock 模式；real 模式可换 LLM 分类器）。"""
    scores: dict[str, int] = {}
    for intent, patterns in INTENT_PATTERNS.items():
        score = 0
        for p in patterns:
            if ".*" in p or "*" in p:
                if re.search(p, query):
                    score += 2
            elif p in query:
                score += 1
        if score:
            scores[intent] = score
    if not scores:
        return "data_query"
    best = max(scores.values())
    for intent in INTENT_PRIORITY:
        if scores.get(intent) == best:
            return intent
    return max(scores, key=scores.get)


def build_plan(intent: str, query: str) -> list[str]:
    """根据意图生成 Agent 执行计划（对应文档 3.5 路由 Agent 列）。"""
    # 复合触发：明示要"出报告" → 串上 ReportAgent
    wants_report = (
        "全流程" in query or "从数据采集" in query
        or (("生成" in query or "撰写" in query or "写" in query) and "报告" in query)
    )
    if intent == "data_query":
        plan = ["data_agent"]
    elif intent == "research_query":
        plan = ["analysis_agent"]
    elif intent == "report_generate":
        plan = ["data_agent", "analysis_agent", "report_agent"]
    else:
        # market_analysis / stock_analysis / macro_analysis / industry_compare /
        # portfolio_advice / financial_diagnosis：先采集再分析
        plan = ["data_agent", "analysis_agent"]
    if wants_report and "report_agent" not in plan:
        plan.append("report_agent")
    return plan


# ---------------------------------------------------------------------------
# 顶层图节点
# ---------------------------------------------------------------------------

def orchestrator_node(state: ResearchState) -> dict:
    """意图识别 + 路由决策。"""
    tid = state.get("thread_id", "")
    intent = classify_intent(state["query"])
    plan = build_plan(intent, state["query"])
    emit_node_progress(tid, "Orchestrator", "orchestrator",
                       f"意图识别：{intent}，执行计划：{' → '.join(plan)}",
                       {"intent": intent, "plan": plan})
    return {"intent": intent, "plan": plan, "stage": "routing",
            "progress": list(state.get("progress", []))
            + [f"[Orchestrator] 意图={intent}，计划={' → '.join(plan)}"]}


def summarize_node(state: ResearchState) -> dict:
    """汇总三个 Agent 的产出，生成最终答复。"""
    tid = state.get("thread_id", "")
    emit_node_progress(tid, "Orchestrator", "summarize", "汇总各 Agent 结果")
    parts: list[str] = []

    if state.get("data_summary"):
        parts.append(f"📊 **数据采集摘要**\n{state['data_summary']}")
    if state.get("financial_indicators"):
        ind = state["financial_indicators"].get("indicators", {})
        rows = "\n".join(f"| {k} | {v} |" for k, v in ind.items())
        parts.append(f"📈 **核心财务指标（2025Q3）**\n| 指标 | 数值 |\n| --- | --- |\n{rows}")
    if state.get("valuation_result"):
        v = state["valuation_result"]
        parts.append(
            f"💰 **综合估值**\n- 综合目标价：**{v.get('composite_value')} 元**"
            f"（现价 {v.get('current_price')} 元，上行空间 {v.get('upside_pct')}%）\n"
            f"- PE 估值 {v.get('pe_value') or '不适用'} / PS 估值 {v.get('ps_value') or '不适用'}"
            f" / 成长估值 {v.get('growth_value')}")
    if state.get("risk_result"):
        r = state["risk_result"]
        scores = r.get("risk_scores", {})
        risk_rows = "\n".join(f"| {k} | {v} |" for k, v in scores.items())
        parts.append(
            f"⚠️ **风险评估**\n| 维度 | 评分 |\n| --- | --- |\n{risk_rows}\n"
            f"- 综合风险等级：**{r.get('overall_risk')}**\n"
            f"- Beta {r.get('beta')} / 最大回撤 {r.get('max_drawdown')}% / "
            f"夏普 {r.get('sharpe')} / 年化波动 {r.get('volatility')}% / VaR95 {r.get('var_95')}%")
    if state.get("investment_logic"):
        logic = state["investment_logic"]
        parts.append(
            f"🧠 **投资逻辑**\n{logic.get('core_logic', '')}\n"
            f"- 催化剂：{'、'.join(logic.get('catalysts', []))}\n"
            f"- 评级：**{state.get('rating', '中性')}**")
    if state.get("review_decision") == "approve":
        parts.append("✅ 基金经理已确认分析结论。")
    if state.get("formatted_report"):
        parts.append(f"📝 **基金研究报告已生成并通过合规审查**（报告号 {state.get('report_id')}，"
                     f"已推送基金经理审核）")
    if not parts:
        parts.append("未产出结构化结果，请补充查询条件。")

    final = "\n\n".join(parts)
    return {"final_answer": final, "stage": "done",
            "progress": list(state.get("progress", [])) + ["[Orchestrator] summarize: 完成"]}


# ---------------------------------------------------------------------------
# 条件边路由
# ---------------------------------------------------------------------------

def _next_in_plan(state: ResearchState, after: str | None) -> str:
    plan = state.get("plan", [])
    done = state.get("agents_done", [])
    start_idx = -1 if after is None else plan.index(after)
    for agent in plan[start_idx + 1:]:
        if agent not in done:
            return agent
    return "summarize"


def route_start(state: ResearchState) -> str:
    return _next_in_plan(state, None)


def route_after_data(state: ResearchState) -> str:
    return _next_in_plan(state, "data_agent")


def route_after_analysis(state: ResearchState) -> str:
    return _next_in_plan(state, "analysis_agent")


# ---------------------------------------------------------------------------
# 顶层图组装
# ---------------------------------------------------------------------------

def build_fund_agent_graph():
    """组装 FundAgent AI 顶层图（三个子图 + Orchestrator + summarize）。"""
    data_graph = build_data_agent().compile()
    analysis_graph = build_analysis_agent().compile()
    report_graph = build_report_agent().compile()

    g = StateGraph(ResearchState)
    # 长对话上下文治理：messages 超阈值时增量摘要压缩（未超阈值零成本）
    g.add_node("maybe_compress_history", maybe_compress_history)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("data_agent", data_graph)
    g.add_node("analysis_agent", analysis_graph)
    g.add_node("report_agent", report_graph)
    g.add_node("summarize", summarize_node)

    g.add_edge(START, "maybe_compress_history")
    g.add_edge("maybe_compress_history", "orchestrator")
    g.add_conditional_edges("orchestrator", route_start,
                            {"data_agent": "data_agent", "analysis_agent": "analysis_agent",
                             "report_agent": "report_agent"})
    g.add_conditional_edges("data_agent", route_after_data,
                            {"analysis_agent": "analysis_agent", "report_agent": "report_agent",
                             "summarize": "summarize"})
    g.add_conditional_edges("analysis_agent", route_after_analysis,
                            {"report_agent": "report_agent", "summarize": "summarize"})
    g.add_edge("report_agent", "summarize")
    g.add_edge("summarize", END)
    return g
