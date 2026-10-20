# -*- coding: utf-8 -*-
"""
ReportAgent（投资建议生成 Agent）—— 7 节点 LangGraph 状态图
==========================================================
节点：load_analysis_result → generate_report_body → compliance_check →
      format_report → add_disclaimer → save_and_push → log_audit_trail

条件边：compliance_check 合规通过 → format_report；
        不通过 → 回到 generate_report_body 重新生成（最多 2 次）。
"""
from __future__ import annotations

import time
from typing import Literal

from langgraph.graph import END, START, StateGraph

from ..db import store
from ..tools import api_tools, llm_tools, rule_tools
from .events import emit_node_progress
from .state import ResearchState

AGENT = "ReportAgent"
MAX_RETRY = 2

AUTHOR_INFO = {"name": "FundAgent AI 研究助理", "qualification_no": "A2025MOCK001"}


def _progress(state: ResearchState, node: str, msg: str, extra: dict | None = None) -> dict:
    tid = state.get("thread_id", "")
    emit_node_progress(tid, AGENT, node, msg, extra)
    return {"progress": list(state.get("progress", [])) + [f"[{AGENT}] {node}: {msg}"]}


def load_analysis_result(state: ResearchState) -> dict:
    """加载 AnalysisAgent 的分析结果（投资逻辑/估值/风险）。"""
    upd = _progress(state, "load_analysis_result", "加载上游分析结果")
    if state.get("analysis_record_id") and not state.get("valuation_result"):
        # 跨 Agent 场景：从长期记忆读取（演示 get_research_result 工具）
        try:
            saved = api_tools.get_research_result(
                state["analysis_record_id"], state["department_id"], state["user_id"])
            return {**upd, "valuation_result": saved.get("valuation", {}),
                    "risk_result": saved.get("risk", {}),
                    "investment_logic": saved.get("investment_logic", {}),
                    "rating": saved.get("rating")}
        except Exception:  # noqa: BLE001
            pass
    return upd


async def generate_report_body(state: ResearchState) -> dict:
    """LLM 生成报告正文。"""
    retry = state.get("retry_counts", {}).get("compliance", 0)
    upd = _progress(state, "generate_report_body",
                    "LLM 生成基金研究报告正文" + ("（合规整改后重新生成）" if retry else ""))
    reports = state.get("research_reports", [])
    analysis_result = {
        "stock_code": (reports[0]["stock_code"] if reports else ""),
        "stock_name": (reports[0]["stock_name"] if reports else "组合/主题"),
        "rating": state.get("rating", "中性"),
        "investment_logic": state.get("investment_logic", {}),
        "valuation": state.get("valuation_result", {}),
        "financial_indicators": state.get("financial_indicators", {}),
        "risk": state.get("risk_result", {}),
    }
    body = await llm_tools.llm_generate_report(analysis_result, "company-standard-v1")
    return {**upd, "report_body": body}


def compliance_check(state: ResearchState) -> dict:
    """合规审查：信息隔离墙 / 利益冲突 / 免责声明（规则引擎）。"""
    upd = _progress(state, "compliance_check", "合规审查（隔离墙/利益冲突/免责声明）")
    result = rule_tools.compliance_filter(state.get("report_body", ""), AUTHOR_INFO)
    retries = state.get("retry_counts", {})
    return {**upd, "compliance_result": result,
            "retry_counts": {**retries, "compliance":
                             retries.get("compliance", 0) + (0 if result["is_compliant"] else 1)}}


def format_report(state: ResearchState) -> dict:
    """按公司模板格式化。"""
    upd = _progress(state, "format_report", "按公司模板格式化报告")
    return {**upd, "formatted_report":
            rule_tools.format_report_template(state.get("report_body", ""), "company-standard-v1")}


async def add_disclaimer(state: ResearchState) -> dict:
    """附加合规免责声明。"""
    upd = _progress(state, "add_disclaimer", "附加免责声明与分析师声明")
    disclaimer = await llm_tools.llm_generate_disclaimer(AUTHOR_INFO)
    return {**upd, "formatted_report":
            state.get("formatted_report", "") + disclaimer}


def save_and_push(state: ResearchState) -> dict:
    """保存到基金报告库 + 推送基金经理。"""
    upd = _progress(state, "save_and_push", "保存报告并推送基金经理审核")
    reports = state.get("research_reports", [])
    title = f"{(reports[0]['stock_name'] if reports else '主题')}基金研究报告"
    r = api_tools.save_report_to_repository(
        state["department_id"], state["user_id"], state["thread_id"],
        title=title, body=state.get("formatted_report", ""),
        report_type="单只基金报告" if reports else "主题报告",
        compliance=state.get("compliance_result", {}))
    push = api_tools.push_to_fund_manager(r["report_id"], ["fm_001"])
    return {**upd, "report_id": r["report_id"], "push_result": push}


def log_audit_trail(state: ResearchState) -> dict:
    """记录审计日志（留痕 5 年）。"""
    upd = _progress(state, "log_audit_trail", "记录审计留痕")
    trail = {
        "report_id": state.get("report_id"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "compliance": state.get("compliance_result", {}),
        "pushed_to": state.get("push_result", {}).get("targets", []),
        "analyst": f"{state['department_id']}/{state['user_id']}",
    }
    api_tools.save_audit_log(state["department_id"], state["user_id"], "report_generated", trail)
    return {**upd, "audit_trail": trail, "agents_done":
            list(state.get("agents_done", [])) + [AGENT]}


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------

def route_compliance(state: ResearchState) -> Literal["format_report", "generate_report_body"]:
    """合规通过 → 格式化；不通过且未超重试上限 → 重新生成。"""
    if state.get("compliance_result", {}).get("is_compliant"):
        return "format_report"
    retry = state.get("retry_counts", {}).get("compliance", 0)
    if retry <= MAX_RETRY:
        return "generate_report_body"
    return "format_report"  # 超限降级：标记不合规仍然输出，供人工复核


def build_report_agent() -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("load_analysis_result", load_analysis_result)
    g.add_node("generate_report_body", generate_report_body)
    g.add_node("compliance_check", compliance_check)
    g.add_node("format_report", format_report)
    g.add_node("add_disclaimer", add_disclaimer)
    g.add_node("save_and_push", save_and_push)
    g.add_node("log_audit_trail", log_audit_trail)

    g.add_edge(START, "load_analysis_result")
    g.add_edge("load_analysis_result", "generate_report_body")
    g.add_edge("generate_report_body", "compliance_check")
    g.add_conditional_edges("compliance_check", route_compliance,
                            {"format_report": "format_report",
                             "generate_report_body": "generate_report_body"})
    g.add_edge("format_report", "add_disclaimer")
    g.add_edge("add_disclaimer", "save_and_push")
    g.add_edge("save_and_push", "log_audit_trail")
    g.add_edge("log_audit_trail", END)
    return g
