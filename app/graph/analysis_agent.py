# -*- coding: utf-8 -*-
"""
AnalysisAgent（基金分析 Agent）—— 9 节点 LangGraph 状态图
=========================================================
节点：load_research_pool → parse_research_report → calculate_financial_indicators →
      build_valuation_model → assess_risk_level → check_risk_threshold(路由) →
      generate_investment_logic → human_review(interrupt) → save_analysis_result

条件边：
  - check_risk_threshold：风险可控 → generate_investment_logic；
                          风险过高 → 回到 load_research_pool 换标的（最多 2 次）
  - human_review：基金经理确认 → save_analysis_result；
                  退回修改 → 带意见回到 generate_investment_logic（最多 2 次）
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import mock_data
from ..db import store
from ..tools import api_tools, llm_tools, mcp_tools, rule_tools
from .events import emit_node_progress
from .state import ResearchState

AGENT = "AnalysisAgent"
MAX_RETRY = 2

INDICATOR_LIST = ["revenue", "net_profit", "gross_margin", "net_margin", "roe", "debt_ratio", "eps"]


def _progress(state: ResearchState, node: str, msg: str, extra: dict | None = None) -> dict:
    tid = state.get("thread_id", "")
    emit_node_progress(tid, AGENT, node, msg, extra)
    return {"progress": list(state.get("progress", [])) + [f"[{AGENT}] {node}: {msg}"]}


def _base_primary(state: ResearchState) -> str:
    """不考虑换标的旋转的主标的（用于研报池排序，保证池顺序在多次重试间稳定）。"""
    hit = mock_data.find_stock_in_text(state.get("query", ""))
    if hit:
        return hit["stock_code"].split(".")[0]
    codes = state.get("stock_codes") or []
    if codes:
        return codes[0].split(".")[0]
    reports = state.get("research_reports") or []
    if reports:
        return reports[0]["stock_code"].split(".")[0]
    return "300750"


def _primary_code(state: ResearchState) -> str:
    """主分析标的：① 风险换标的重试 → 研报池中下一只；② query 中点名的标的；
    ③ stock_codes[0]；④ 研报池第一个。
    重试耗尽（候选标的全部高风险）→ 回到最初标的，带风险标记降级继续。"""
    retry = state.get("retry_counts", {}).get("risk", 0)
    reports = state.get("research_reports") or []
    if retry and len(reports) > 1:
        if retry <= MAX_RETRY:
            # 换标的：与研报池旋转联动，保证估值/财务/逻辑分析同一只标的
            return reports[min(retry, len(reports) - 1)]["stock_code"].split(".")[0]
        return _base_primary(state)
    return _base_primary(state)


def _pool_index(state: ResearchState) -> int:
    """当前应解析的研报在池中的下标（与 _primary_code 的换标的旋转保持一致）。"""
    retry = state.get("retry_counts", {}).get("risk", 0)
    reports = state.get("research_reports") or []
    if not reports:
        return 0
    if retry <= MAX_RETRY:
        return min(retry, len(reports) - 1)
    return 0  # 重试耗尽 → 回到池首（最初标的）


def load_research_pool(state: ResearchState) -> dict:
    """加载基金报告池（向量检索 + 详情）。"""
    retry = state.get("retry_counts", {}).get("risk", 0)
    if state.get("final_pass"):
        note = "（回到最初标的，最终分析）"
    elif retry:
        note = "（换标的重试）"
    else:
        note = ""
    upd = _progress(state, "load_research_pool", f"检索基金报告池{note}")
    reports = mcp_tools.search_research_reports(state["query"], top_k=3)
    if not reports:
        code = _primary_code(state)
        reports = mcp_tools.search_research_reports(mock_data.find_stock(code)["stock_name"], top_k=3)
    # 主标的研报排到池首（按基址排序，不受换标的旋转影响，保证多次重试间池顺序稳定）
    primary = _base_primary(state)
    reports.sort(key=lambda r: 0 if r["stock_code"].split(".")[0] == primary else 1)
    # 确保有财务数据（research_query 意图单独路由到本 Agent 时未经 DataAgent）
    fin = dict(state.get("financial_data", {}))
    for rpt in reports[:1]:
        code = rpt["stock_code"].split(".")[0]
        if code not in fin:
            got = mcp_tools.get_fund_report(code)
            if got.get("success"):
                stock = mock_data.find_stock(code)
                fin[code] = {p: rule_tools.align_financial_data([dict(v)])
                             for p, v in stock["financials"].items()}
    return {**upd, "research_reports": reports, "financial_data": fin}


async def parse_research_report(state: ResearchState) -> dict:
    """LLM 深度解析基金报告（换标的重试时解析池中下一只）。"""
    reports = state.get("research_reports", [])
    if not reports:
        return _progress(state, "parse_research_report", "基金报告池为空，跳过解析")
    idx = _pool_index(state)
    upd = _progress(state, "parse_research_report",
                    f"LLM 解析《{reports[idx]['title']}》"
                    + ("（换标的后重新解析）" if idx else ""))
    analysis = await llm_tools.llm_parse_research_report(
        reports[idx]["raw_text"], reports[idx]["stock_code"])
    return {**upd, "report_analysis": analysis}


def calculate_financial_indicators(state: ResearchState) -> dict:
    """计算核心财务指标。"""
    code = _primary_code(state)
    statements = state.get("financial_data", {}).get(code)
    if not statements:
        stock = mock_data.find_stock(code)
        statements = {p: rule_tools.align_financial_data([dict(v)])
                      for p, v in stock["financials"].items()}
    upd = _progress(state, "calculate_financial_indicators", f"计算 {code} 财务指标")
    result = rule_tools.financial_indicator_calc(statements, INDICATOR_LIST)
    return {**upd, "financial_indicators": result}


def build_valuation_model(state: ResearchState) -> dict:
    """构建多方法综合估值。"""
    code = _primary_code(state)
    stock = mock_data.find_stock(code)
    statements = state.get("financial_data", {}).get(code, stock["financials"])
    market_row = next((m for m in state.get("market_data", [])
                       if m["stock_code"].split(".")[0] == code), None)
    market_info = dict(stock["quote"])
    market_info["industry_l1"] = stock["industry_l1"]
    if market_row:
        market_info.update(market_row["quote"])
    upd = _progress(state, "build_valuation_model", "多方法综合估值（PE/PS/成长加权）")
    result = rule_tools.valuation_model_calc(statements, market_info, ["pe", "ps", "growth"])
    return {**upd, "valuation_result": result}


async def assess_risk_level(state: ResearchState) -> dict:
    """风险评估：规则引擎算指标 + LLM 评五维度。"""
    code = _primary_code(state)
    stock = mock_data.find_stock(code)
    market_row = next((m for m in state.get("market_data", [])
                       if m["stock_code"].split(".")[0] == code), None)
    prices = market_row["price_series"] if market_row else mock_data.gen_price_series(code)
    benchmark = mock_data.gen_benchmark_series()
    upd = _progress(state, "assess_risk_level", "计算风险指标 + LLM 五维度风险评估")
    metrics = rule_tools.risk_indicator_calc(prices, benchmark)
    industry_data = {
        "industry_risk": "中高" if stock["industry_l2"] in ("电池", "光伏设备") else "中",
        "policy_risk": "中",
        "risk_notes": stock["risk_notes"],
    }
    llm_part = await llm_tools.llm_assess_risk(
        state.get("financial_data", {}).get(code, stock["financials"]),
        industry_data, state.get("valuation_result", {}))
    return {**upd, "risk_result": {**metrics, **llm_part}}


def check_risk_threshold(state: ResearchState) -> dict:
    """风险阈值判断（条件边依据）。文档：估值偏离度 > 30% / 综合风险"高" → 过高。"""
    risk = state.get("risk_result", {})
    valuation = state.get("valuation_result", {})
    overall = risk.get("overall_risk", "中")
    upside = valuation.get("upside_pct")
    deviation = abs(upside) if upside is not None else 0
    ok = overall != "高" and (upside is None or upside > -10) and deviation <= 30
    retries = state.get("retry_counts", {})
    risk_retry = retries.get("risk", 0)
    exhausted = (not ok) and risk_retry > MAX_RETRY
    upd = _progress(state, "check_risk_threshold",
                    f"风险等级 {overall}，估值偏离 {upside}% → "
                    + ("可控" if ok else ("候选标的均不可行，回到最初标的降级继续" if exhausted
                                      else "过高，需换标的")))
    return {**upd, "risk_threshold_ok": ok,
            "retry_counts": {**retries, "risk": risk_retry + (0 if ok else 1)},
            "final_pass": bool(state.get("final_pass")) or exhausted}


async def generate_investment_logic(state: ResearchState) -> dict:
    """提炼投资逻辑 + 评级。"""
    retry = state.get("retry_counts", {}).get("review", 0)
    comments = state.get("review_comments", "")
    upd = _progress(state, "generate_investment_logic",
                    "LLM 提炼投资逻辑（驱动因素/催化剂/预期差）" + ("（按审核意见重新生成）" if comments else ""))
    code = _primary_code(state)
    stock = mock_data.find_stock(code)
    analysis_result = {
        "stock_code": stock["stock_code"], "stock_name": stock["stock_name"],
        "valuation": state.get("valuation_result", {}),
        "risk": state.get("risk_result", {}),
        "report_analysis": state.get("report_analysis", {}),
    }
    logic = await llm_tools.llm_extract_investment_logic(
        analysis_result,
        market_context={"catalysts": ["季报业绩披露", "行业政策落地", "新技术量产"]})
    if comments:
        logic["review_adjustment"] = f"已按基金经理意见调整：{comments}"
    # 评级规则：综合估值上行空间 → 买入/增持/中性/减持
    upside = state.get("valuation_result", {}).get("upside_pct") or 0
    rating = "买入" if upside > 20 else ("增持" if upside > 10 else ("中性" if upside > 0 else "减持"))
    return {**upd, "investment_logic": logic, "rating": rating,
            "retry_counts": {**state.get("retry_counts", {}), "review": retry}}


def human_review(state: ResearchState) -> dict:
    """Human-in-the-Loop：基金经理确认分析结论（LangGraph interrupt）。"""
    tid = state.get("thread_id", "")
    valuation = state.get("valuation_result", {})
    payload = {
        "question": "请确认以下分析结论",
        "stock": f"{state.get('research_reports', [{}])[0].get('stock_name', '')}"
                 f"（{_primary_code(state)}）",
        "rating": state.get("rating"),
        "target_price": valuation.get("composite_value"),
        "upside_pct": valuation.get("upside_pct"),
        "risk_level": state.get("risk_result", {}).get("overall_risk"),
        "investment_logic": state.get("investment_logic", {}).get("core_logic", ""),
    }
    emit_node_progress(tid, AGENT, "human_review",
                       "等待基金经理确认（interrupt 暂停）",
                       {"review_required": True, "review_payload": payload})
    decision = interrupt(payload)  # 暂停，等待 Command(resume=...)
    retries = state.get("retry_counts", {})
    rejected = decision.get("decision") != "approve"
    return {"review_decision": decision.get("decision", "approve"),
            "review_comments": decision.get("comments", ""),
            "retry_counts": {**retries, "review": retries.get("review", 0) + (1 if rejected else 0)}}


def save_analysis_result(state: ResearchState) -> dict:
    """持久化分析结果 + 更新单只基金跟踪历史（长期记忆）。"""
    code = _primary_code(state)
    stock = mock_data.find_stock(code)
    valuation = state.get("valuation_result", {})
    upd = _progress(state, "save_analysis_result", "分析结果入库 + 更新跟踪历史")
    result = {
        "stock_code": stock["stock_code"], "stock_name": stock["stock_name"],
        "rating": state.get("rating"), "target_price": valuation.get("composite_value"),
        "risk_level": state.get("risk_result", {}).get("overall_risk"),
        "valuation": valuation, "risk": state.get("risk_result", {}),
        "investment_logic": state.get("investment_logic", {}),
        "financial_indicators": state.get("financial_indicators", {}),
    }
    record_id = api_tools.save_research_result(
        state["department_id"], state["user_id"], state["thread_id"], result)["record_id"]
    store.upsert_tracking(state["department_id"], state["user_id"], {
        "stock_code": stock["stock_code"], "stock_name": stock["stock_name"],
        "industry_l1": stock["industry_l1"], "industry_l2": stock["industry_l2"],
        "rating": result["rating"], "target_price": result["target_price"],
        "valuation": valuation, "risk": state.get("risk_result", {}),
    })
    return {**upd, "analysis_record_id": record_id, "agents_done":
            list(state.get("agents_done", [])) + [AGENT]}


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------

def route_risk(state: ResearchState) -> Literal["generate_investment_logic", "load_research_pool"]:
    """风险可控 → 生成投资逻辑；过高且未超重试上限 → 换标的；
    超限且未降级过 → 回最初标的最终分析一遍；已降级 → 带风险标记继续。"""
    if state.get("risk_threshold_ok"):
        return "generate_investment_logic"
    retry = state.get("retry_counts", {}).get("risk", 0)
    if retry <= MAX_RETRY:
        return "load_research_pool"
    if not state.get("final_pass"):
        # 最终一遍：_primary_code/_pool_index 检测到 final_pass 后已回迁到最初标的
        return "load_research_pool"
    return "generate_investment_logic"  # 降级：带高风险标记继续


def route_review(state: ResearchState) -> Literal["save_analysis_result", "generate_investment_logic"]:
    """基金经理确认 → 保存；退回修改 → 带意见重新生成（最多 2 次）。"""
    if state.get("review_decision") == "approve":
        return "save_analysis_result"
    retry = state.get("retry_counts", {}).get("review", 0)
    if retry <= MAX_RETRY:
        return "generate_investment_logic"
    return "save_analysis_result"  # 超限降级：保存并标记待人工复核


def build_analysis_agent() -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("load_research_pool", load_research_pool)
    g.add_node("parse_research_report", parse_research_report)
    g.add_node("calculate_financial_indicators", calculate_financial_indicators)
    g.add_node("build_valuation_model", build_valuation_model)
    g.add_node("assess_risk_level", assess_risk_level)
    g.add_node("check_risk_threshold", check_risk_threshold)
    g.add_node("generate_investment_logic", generate_investment_logic)
    g.add_node("human_review", human_review)
    g.add_node("save_analysis_result", save_analysis_result)

    g.add_edge(START, "load_research_pool")
    g.add_edge("load_research_pool", "parse_research_report")
    g.add_edge("parse_research_report", "calculate_financial_indicators")
    g.add_edge("calculate_financial_indicators", "build_valuation_model")
    g.add_edge("build_valuation_model", "assess_risk_level")
    g.add_edge("assess_risk_level", "check_risk_threshold")
    g.add_conditional_edges("check_risk_threshold", route_risk,
                            {"generate_investment_logic": "generate_investment_logic",
                             "load_research_pool": "load_research_pool"})
    g.add_edge("generate_investment_logic", "human_review")
    g.add_conditional_edges("human_review", route_review,
                            {"save_analysis_result": "save_analysis_result",
                             "generate_investment_logic": "generate_investment_logic"})
    g.add_edge("save_analysis_result", END)
    return g
