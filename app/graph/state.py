# -*- coding: utf-8 -*-
"""三个 Agent 共享的 State 定义（同一 schema 使子图可以直接作为节点嵌入父图）。"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class ResearchState(TypedDict, total=False):
    # ---- 上下文与隔离 ----
    messages: Annotated[list, add_messages]
    conversation_summary: str     # 长对话增量摘要（压缩旧消息后的历史，见 summarize.py）
    department_id: str            # 部门隔离（信息隔离墙）
    user_id: str                  # 用户隔离
    thread_id: str                # 会话/任务隔离（Checkpointer 维度）
    query: str                    # 用户原始输入

    # ---- Orchestrator 编排 ----
    intent: str                   # 主意图（8 类之一）
    plan: list[str]               # Agent 执行计划，如 ["data_agent", "analysis_agent", "report_agent"]
    agents_done: list[str]        # 已完成的 Agent
    stage: str                    # 当前阶段（data_collection/analysis/report/done）

    # ---- DataAgent ----
    stock_codes: list[str]
    data_sources: list[str]
    market_data: list[dict]
    financial_data: dict          # code -> financial statements（对齐后）
    financial_data_multi_source: dict   # code -> period -> {wind/announcement}（对齐前，一致性校验用）
    announcements: list[dict]
    industry_classification: dict
    consistency_result: dict
    vector_ids: list[str]
    data_summary: str

    # ---- AnalysisAgent ----
    research_reports: list[dict]
    report_analysis: dict
    financial_indicators: dict
    valuation_result: dict
    risk_result: dict
    risk_threshold_ok: bool
    investment_logic: dict
    rating: str
    review_decision: str          # approve / reject
    review_comments: str
    analysis_record_id: str

    # ---- ReportAgent ----
    report_body: str
    compliance_result: dict
    formatted_report: str
    report_id: str
    push_result: dict
    audit_trail: dict

    # ---- 通用 ----
    retry_counts: dict            # 各条件边重试计数（一致性校验/合规审查/换标的）
    final_pass: bool              # 换标的重试耗尽后回到最初标的的最终一遍（降级）
    progress: list[str]           # 节点执行流水
    final_answer: str             # 汇总给用户的最终答复
