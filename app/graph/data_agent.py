# -*- coding: utf-8 -*-
"""
DataAgent（净值数据采集 Agent）—— 8 节点 LangGraph 状态图
=========================================================
节点：load_data_sources → fetch_market_data → fetch_financial_data →
      fetch_announcements → classify_industry → validate_consistency →
      store_vector_db → generate_data_summary

条件边：validate_consistency 通过 → store_vector_db；
        不通过（差异 > 阈值）→ 回到 fetch_financial_data 重新对齐（最多 2 次）。
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from .. import mock_data
from ..tools import api_tools, llm_tools, mcp_tools, rule_tools
from .events import emit_node_progress
from .state import ResearchState

AGENT = "DataAgent"
MAX_RETRY = 2


def _progress(state: ResearchState, node: str, msg: str, extra: dict | None = None) -> dict:
    tid = state.get("thread_id", "")
    emit_node_progress(tid, AGENT, node, msg, extra)
    return {"progress": list(state.get("progress", [])) + [f"[{AGENT}] {node}: {msg}"]}


def load_data_sources(state: ResearchState) -> dict:
    """加载数据源配置，确定本次采集范围（标的池）。"""
    upd = _progress(state, "load_data_sources", "加载数据源配置，确定采集范围")
    stocks = mock_data.search_stocks_by_keyword(state["query"])
    sources = ["Wind 数据终端", "Bloomberg", "巨潮资讯网(基金公告)", "央行统计数据"]
    api_tools.get_market_quote(stocks[0]["stock_code"])  # 确认标的信息
    return {**upd,
            "stock_codes": [s["stock_code"] for s in stocks],
            "data_sources": sources,
            "stage": "data_collection"}


def fetch_market_data(state: ResearchState) -> dict:
    """采集实时净值数据并标准化。"""
    upd = _progress(state, "fetch_market_data", f"采集 {len(state['stock_codes'])} 只标的行情数据")
    out = []
    for code in state["stock_codes"]:
        rows = api_tools.fetch_market_data_api([code])
        for row in rows:
            row["quote"] = rule_tools.format_data_standard(row["quote"], source="Wind")
            out.append(row)
    return {**upd, "market_data": out}


def fetch_financial_data(state: ResearchState) -> dict:
    """采集财务报表并对齐多源口径（重试时切换到公告口径）。"""
    retry = state.get("retry_counts", {}).get("consistency", 0)
    upd = _progress(state, "fetch_financial_data",
                    f"采集财务数据（第 {retry + 1} 次，口径：{'公告' if retry else 'Wind'}）")
    fin: dict[str, dict] = {}
    raw: dict[str, dict] = {}
    for code in state["stock_codes"]:
        rpt = mcp_tools.get_fund_report(code)
        if not rpt.get("success"):
            continue
        stock = mock_data.find_stock(code)
        statements, raw_sources = {}, {}
        for period, vals in stock["financials"].items():
            # 模拟多源：Wind 口径 vs 公告口径（首次采集公告 EPS 有 1.2% 偏差 → 触发校验回路）
            wind_v = dict(vals)
            ann_v = dict(vals)
            ann_v["eps"] = round(vals["eps"] * (0.988 if retry == 0 else 1.0), 3)
            statements[period] = rule_tools.align_financial_data([wind_v, ann_v])
            raw_sources[period] = {"wind": wind_v, "announcement": ann_v}
        fin[code] = statements
        raw[code] = raw_sources
    return {**upd, "financial_data": fin, "financial_data_multi_source": raw}


async def fetch_announcements(state: ResearchState) -> dict:
    """采集基金公告并用 LLM 解析关键信息（多标的并行，对应文档多源并行采集）。"""
    import asyncio

    upd = _progress(state, "fetch_announcements", "采集并解析基金公告（并行）")
    all_anns: list[tuple[dict, dict]] = []
    for code in state["stock_codes"]:
        for ann in mcp_tools.query_announcements(code):
            all_anns.append((code, ann))

    async def _parse_one(code: str, ann: dict) -> dict:
        parsed = await llm_tools.llm_parse_announcement(ann["summary"], ann["ann_type"])
        return {**ann, "parsed": parsed}

    anns = await asyncio.gather(*[_parse_one(c, a) for c, a in all_anns])
    return {**upd, "announcements": list(anns)}


def classify_industry(state: ResearchState) -> dict:
    """按申万一级/二级行业分类。"""
    upd = _progress(state, "classify_industry", "行业分类标准化")
    result = {}
    for code in state["stock_codes"]:
        stock = mock_data.find_stock(code)
        result[code] = rule_tools.industry_classify(code, stock["stock_name"])
    return {**upd, "industry_classification": result}


def validate_consistency(state: ResearchState) -> dict:
    """多源数据一致性校验（Wind vs Bloomberg vs 公告原始数据交叉比对）。"""
    upd = _progress(state, "validate_consistency", "多源数据一致性交叉校验")
    # 平铺结构：{字段名: {数据源: 数值}}（对应 data_consistency_check 入参）
    multi: dict[str, dict] = {}
    for code, sources in state.get("financial_data_multi_source", {}).items():
        wind = sources.get("2025Q3", {}).get("wind", {})
        ann = sources.get("2025Q3", {}).get("announcement", {})
        multi[f"{code}.营业收入"] = {
            "wind": wind.get("revenue"), "bloomberg": wind.get("revenue"),
            "announcement": ann.get("revenue")}
        multi[f"{code}.EPS"] = {
            "wind": wind.get("eps"),
            "bloomberg": round(wind.get("eps", 0) * 1.005, 3),  # Bloomberg 0.5% 四舍五入差异
            "announcement": ann.get("eps")}
    result = rule_tools.data_consistency_check(multi)
    retries = state.get("retry_counts", {})
    return {**upd, "consistency_result": result,
            "retry_counts": {**retries, "consistency":
                             retries.get("consistency", 0) + (0 if result["is_consistent"] else 1)}}


def store_vector_db(state: ResearchState) -> dict:
    """清洗后的数据结构化存入向量库。"""
    upd = _progress(state, "store_vector_db", "存入 Milvus 向量库（mock）")
    ids = []
    for code in state["stock_codes"]:
        stock = mock_data.find_stock(code)
        text = f"{stock['stock_name']} {code} {stock['industry_l1']} {stock['industry_l2']}"
        r = mcp_tools.store_to_vector_db({"stock_code": code, "_text": text}, category="stock_profile")
        ids.append(r["vector_id"])
    return {**upd, "vector_ids": ids}


async def generate_data_summary(state: ResearchState) -> dict:
    """生成数据采集摘要。"""
    upd = _progress(state, "generate_data_summary", "生成数据采集摘要")
    log = {
        "stocks": [{"stock_code": c, "stock_name": mock_data.find_stock(c)["stock_name"]}
                   for c in state["stock_codes"]],
        "market_count": len(state.get("market_data", [])),
        "financial_count": len(state.get("financial_data", {})),
        "announcement_count": len(state.get("announcements", [])),
    }
    summary = await llm_tools.llm_generate_summary(log, state.get("consistency_result", {}))
    return {**upd, "data_summary": summary, "agents_done":
            list(state.get("agents_done", [])) + [AGENT]}


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------

def route_consistency(state: ResearchState) -> Literal["store_vector_db", "fetch_financial_data", "__force__"]:
    """一致性通过 → 存储；不通过且重试未超限 → 重新采集对齐；超限 → 强制存储并标记。"""
    result = state.get("consistency_result", {})
    if result.get("is_consistent"):
        return "store_vector_db"
    retry = state.get("retry_counts", {}).get("consistency", 0)
    if retry <= MAX_RETRY:
        return "fetch_financial_data"
    return "__force__"


def build_data_agent() -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("load_data_sources", load_data_sources)
    g.add_node("fetch_market_data", fetch_market_data)
    g.add_node("fetch_financial_data", fetch_financial_data)
    g.add_node("fetch_announcements", fetch_announcements)
    g.add_node("classify_industry", classify_industry)
    g.add_node("validate_consistency", validate_consistency)
    g.add_node("store_vector_db", store_vector_db)
    g.add_node("generate_data_summary", generate_data_summary)

    g.add_edge(START, "load_data_sources")
    g.add_edge("load_data_sources", "fetch_market_data")
    g.add_edge("fetch_market_data", "fetch_financial_data")
    g.add_edge("fetch_financial_data", "fetch_announcements")
    g.add_edge("fetch_announcements", "classify_industry")
    g.add_edge("classify_industry", "validate_consistency")
    g.add_conditional_edges("validate_consistency", route_consistency,
                            {"store_vector_db": "store_vector_db",
                             "fetch_financial_data": "fetch_financial_data",
                             "__force__": "store_vector_db"})
    g.add_edge("store_vector_db", "generate_data_summary")
    g.add_edge("generate_data_summary", END)
    return g
