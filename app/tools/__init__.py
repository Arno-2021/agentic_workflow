# -*- coding: utf-8 -*-
"""
工具注册表
==========
汇总全部工具（文档宣称 20 个：LLM 6 + 规则引擎 5 + MCP 4 + 外部 API 5；
文档工具表格实际列出更多，实现以表格为准，共 24 个），供 /api/tools 展示与自检。
"""
from __future__ import annotations

from . import api_tools, llm_tools, mcp_tools, rule_tools

TOOL_REGISTRY: dict[str, dict] = {
    # ---- LLM 工具（6）----
    "llm_parse_announcement": dict(fn=llm_tools.llm_parse_announcement, category="LLM 工具", agent="DataAgent",
                                    desc="解析基金公告，提取关键信息"),
    "llm_generate_summary": dict(fn=llm_tools.llm_generate_summary, category="LLM 工具", agent="DataAgent",
                                  desc="生成数据采集摘要"),
    "llm_parse_research_report": dict(fn=llm_tools.llm_parse_research_report, category="LLM 工具", agent="AnalysisAgent",
                                       desc="深度解析基金报告（投资逻辑/假设/目标价/评级）"),
    "llm_assess_risk": dict(fn=llm_tools.llm_assess_risk, category="LLM 工具", agent="AnalysisAgent",
                             desc="五维度风险评估"),
    "llm_extract_investment_logic": dict(fn=llm_tools.llm_extract_investment_logic, category="LLM 工具", agent="AnalysisAgent",
                                          desc="提炼投资逻辑（驱动/催化剂/预期差）"),
    "llm_generate_report": dict(fn=llm_tools.llm_generate_report, category="LLM 工具", agent="ReportAgent",
                                 desc="生成基金研究报告正文"),
    # ---- 规则引擎（9）----
    "format_data_standard": dict(fn=rule_tools.format_data_standard, category="规则引擎", agent="DataAgent",
                                  desc="净值数据标准化"),
    "align_financial_data": dict(fn=rule_tools.align_financial_data, category="规则引擎", agent="DataAgent",
                                  desc="多源财务口径对齐"),
    "industry_classify": dict(fn=rule_tools.industry_classify, category="规则引擎", agent="DataAgent",
                               desc="申万行业分类"),
    "data_consistency_check": dict(fn=rule_tools.data_consistency_check, category="规则引擎", agent="DataAgent",
                                    desc="多源数据一致性校验"),
    "financial_indicator_calc": dict(fn=rule_tools.financial_indicator_calc, category="规则引擎", agent="AnalysisAgent",
                                      desc="财务指标计算（ROE/毛利率等）"),
    "valuation_model_calc": dict(fn=rule_tools.valuation_model_calc, category="规则引擎", agent="AnalysisAgent",
                                  desc="多方法综合估值"),
    "risk_indicator_calc": dict(fn=rule_tools.risk_indicator_calc, category="规则引擎", agent="AnalysisAgent",
                                 desc="风险指标（Beta/回撤/夏普/波动率/VaR）"),
    "compliance_filter": dict(fn=rule_tools.compliance_filter, category="规则引擎", agent="ReportAgent",
                               desc="合规审查（隔离墙/利益冲突/免责声明）"),
    "format_report_template": dict(fn=rule_tools.format_report_template, category="规则引擎", agent="ReportAgent",
                                    desc="报告模板格式化"),
    # ---- MCP 工具（4）----
    "get_fund_report": dict(fn=mcp_tools.get_fund_report, category="MCP 工具", agent="DataAgent",
                             desc="获取财务报表（Wind）"),
    "query_announcements": dict(fn=mcp_tools.query_announcements, category="MCP 工具", agent="DataAgent",
                                 desc="查询基金公告（巨潮资讯）"),
    "store_to_vector_db": dict(fn=mcp_tools.store_to_vector_db, category="MCP 工具", agent="DataAgent",
                                desc="结构化数据存入向量库"),
    "search_research_reports": dict(fn=mcp_tools.search_research_reports, category="MCP 工具", agent="AnalysisAgent",
                                     desc="基金报告库检索（向量+关键词）"),
    # ---- 外部 API 封装（5）----
    "get_market_quote": dict(fn=api_tools.get_market_quote, category="外部 API 封装", agent="DataAgent",
                              desc="查询实时行情（Wind）"),
    "fetch_market_data_api": dict(fn=api_tools.fetch_market_data_api, category="外部 API 封装", agent="DataAgent",
                                   desc="批量采集行情数据（Wind）"),
    "get_report_detail": dict(fn=api_tools.get_report_detail, category="外部 API 封装", agent="AnalysisAgent",
                               desc="查询基金报告详情（基金报告库）"),
    "save_research_result": dict(fn=api_tools.save_research_result, category="外部 API 封装", agent="AnalysisAgent",
                                  desc="保存分析结果（Oracle）"),
    "push_to_fund_manager": dict(fn=api_tools.push_to_fund_manager, category="外部 API 封装", agent="ReportAgent",
                                  desc="推送报告给基金经理（投委会系统）"),
    # ---- ReportAgent 附加外部 API（文档节点表引用）----
    "save_report_to_repository": dict(fn=api_tools.save_report_to_repository, category="外部 API 封装", agent="ReportAgent",
                                       desc="保存报告到基金报告库"),
    "get_research_result": dict(fn=api_tools.get_research_result, category="外部 API 封装", agent="ReportAgent",
                                 desc="读取分析结果（跨 Agent 传递）"),
    "save_audit_log": dict(fn=api_tools.save_audit_log, category="外部 API 封装", agent="ReportAgent",
                            desc="记录审计日志（留痕 5 年）"),
    # ---- LLM 附加（文档 ReportAgent 节点引用）----
    "llm_generate_disclaimer": dict(fn=llm_tools.llm_generate_disclaimer, category="LLM 工具", agent="ReportAgent",
                                     desc="生成合规免责声明"),
}


def tool_inventory() -> list[dict]:
    return [{"name": name, **meta} for name, meta in TOOL_REGISTRY.items()]
