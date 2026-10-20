# -*- coding: utf-8 -*-
"""
LLM 工具（6 个）—— 真实 LLM 模式（默认 Ollama qwen3.5:9b）
==========================================================
文档 3.4：需要"理解"金融领域自然语言的任务交给 LLM。
- real 模式：调用 OpenAI 兼容接口（默认本机 Ollama），提示词按 qwen 系列调优
- 降级：LLM 不可达/解析失败时自动回退模板实现（打警告日志），保证图不中断

注意：本模块工具均为 async，对应 LangGraph 节点也必须是 async。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .. import mock_data
from ..llm import llm_client

logger = logging.getLogger("fundagent.tools")


# ---------------------------------------------------------------------------
# 降级辅助：real 且可达 → LLM；否则/失败 → 模板兜底
# ---------------------------------------------------------------------------

async def _llm_json(system: str, user: str, fallback: Callable[[], Any],
                    max_tokens: int | None = None) -> Any:
    if llm_client.is_mock or not await llm_client.is_available():
        return fallback()
    try:
        data = await llm_client.chat_json(system, user, max_tokens=max_tokens)
        if data:
            return data
        logger.warning("LLM JSON 输出解析失败，降级模板（输入前40字：%s）", user[:40])
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 调用异常，降级模板：%s", e)
    return fallback()


async def _llm_text(system: str, user: str, fallback: Callable[[], str],
                    max_tokens: int | None = None) -> str:
    if llm_client.is_mock or not await llm_client.is_available():
        return fallback()
    try:
        text = await llm_client.chat(system, user, max_tokens=max_tokens)
        if text and not text.startswith("[mock-llm]"):
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 调用异常，降级模板：%s", e)
    return fallback()


# ---------------------------------------------------------------------------
# DataAgent LLM 工具
# ---------------------------------------------------------------------------

async def llm_parse_announcement(announcement_raw_text: str, announcement_type: str) -> dict:
    """用 LLM 解析基金公司基金公告，提取关键信息（业绩/分红/重组/增减持等）。"""

    def _fallback() -> dict:
        event_type = "其他"
        for kw, et in [("季度报告", "业绩披露"), ("年度报告", "业绩披露"), ("分红", "分红"),
                       ("重组", "重组"), ("减持", "减持"), ("增持", "增持"), ("投资建设", "资本开支")]:
            if kw in announcement_raw_text:
                event_type = et
                break
        return {
            "event_type": event_type,
            "key_points": [announcement_raw_text[:80] + ("…" if len(announcement_raw_text) > 80 else "")],
            "sentiment": "positive" if any(k in announcement_raw_text for k in ("增长", "提升", "突破", "顺利")) else "neutral",
            "parsed_by": "template-fallback",
        }

    return await _llm_json(
        "你是基金研究公告解析器，负责从基金公告中提取结构化关键信息，只输出 JSON。",
        f"公告类型：{announcement_type}\n公告原文：{announcement_raw_text}\n\n"
        '请输出 JSON：{"event_type": "业绩披露|分红|重组|增减持|资本开支|其他", '
        '"key_points": ["要点1", "要点2"], "sentiment": "positive|neutral|negative"}',
        _fallback)


async def llm_generate_summary(data_collection_log: dict, validation_result: dict) -> str:
    """生成数据采集摘要（采集了哪些数据、校验结果、异常标记）。"""
    stocks = data_collection_log.get("stocks", [])
    stock_desc = "、".join(f"{s.get('stock_name')}({s.get('stock_code')})" for s in stocks) or "（无）"

    def _fallback() -> str:
        lines = [
            f"本次共采集 {len(stocks)} 只标的的多源数据：",
            stock_desc,
            f"行情数据 {data_collection_log.get('market_count', 0)} 条，"
            f"财务数据 {data_collection_log.get('financial_count', 0)} 条，"
            f"公告 {data_collection_log.get('announcement_count', 0)} 条。",
            f"一致性校验：{'通过' if validation_result.get('is_consistent') else '未通过'}，"
            f"置信度 {validation_result.get('confidence', 0):.0%}。",
        ]
        if validation_result.get("discrepancies"):
            lines.append("异常标记：" + "；".join(
                f"{d['field']} 在 {d['source']} 差异 {d['diff_pct']}%" for d in validation_result["discrepancies"]))
        else:
            lines.append("异常标记：无。")
        return "\n".join(lines)

    return await _llm_text(
        "你是基金研究数据助理，用简洁的中文概括数据采集结果（150字以内）。",
        f"采集日志：{json.dumps(data_collection_log, ensure_ascii=False)}\n"
        f"校验结果：{json.dumps(validation_result, ensure_ascii=False)}\n"
        "请概括：采集了哪些标的的哪些数据、一致性校验结论、有无异常标记。",
        _fallback, max_tokens=400)


# ---------------------------------------------------------------------------
# AnalysisAgent LLM 工具
# ---------------------------------------------------------------------------

async def llm_parse_research_report(report_raw_text: str, stock_code: str) -> dict:
    """深度解析基金报告内容（提取投资逻辑/关键假设/业绩归因模型/目标价/评级）。"""

    def _fallback() -> dict:
        stock = mock_data.find_stock(stock_code)
        rpt = stock["research_reports"][0] if stock and stock.get("research_reports") else None
        if not rpt:
            return {"logic": report_raw_text[:100], "assumptions": [], "valuation": {},
                    "target_price": None, "rating": "未评级", "parsed_by": "template-fallback"}
        sentences = [s for s in rpt["raw_text"].replace("。", "。\n").split("\n") if s.strip()]
        return {
            "logic": sentences[0] if sentences else "",
            "assumptions": sentences[1:3],
            "valuation": {"method": "多方法加权", "target_price": rpt["target_price"]},
            "target_price": rpt["target_price"],
            "rating": rpt["rating"],
            "source_report": rpt["report_id"],
            "parsed_by": "template-fallback",
        }

    return await _llm_json(
        "你是资深基金研究员，从券商研报中提炼结构化研究结论，只输出 JSON。",
        f"标的代码：{stock_code}\n研报原文：{report_raw_text}\n\n"
        '请输出 JSON：{"logic": "核心投资逻辑（80字内）", "assumptions": ["关键假设1", "关键假设2"], '
        '"valuation": {"method": "估值方法", "target_price": 数字}, '
        '"target_price": 数字, "rating": "买入|增持|中性|减持"}',
        _fallback)


_RISK_ORDER = {"低": 0, "中低": 1, "中": 2, "中高": 3, "高": 4}
_RISK_LABELS = ["低", "中低", "中", "中高", "高"]


def _normalize_risk(result: dict, fallback: dict) -> dict:
    """校正 LLM 输出：五维打分对齐枚举值，综合等级 = 五维最高（与规则引擎阈值逻辑自洽）。"""
    scores = result.get("risk_scores") or {}
    fb_scores = fallback.get("risk_scores", {})
    normalized = {}
    for dim in ("经营风险", "财务风险", "行业风险", "政策风险", "估值风险"):
        v = str(scores.get(dim) or fb_scores.get(dim) or "中").strip()
        if v not in _RISK_ORDER:
            v = "中"
        normalized[dim] = v
    overall = _RISK_LABELS[max(_RISK_ORDER[v] for v in normalized.values())]
    return {
        "risk_scores": normalized,
        "overall_risk": overall,
        "risk_factors": result.get("risk_factors") or fallback.get("risk_factors", []),
        "assessed_by": result.get("assessed_by", "llm"),
    }


async def llm_assess_risk(financial_data: dict, industry_data: dict, valuation_result: dict) -> dict:
    """评估投资风险（经营/财务/行业/政策/估值五维度风险评分）。"""

    def _fallback() -> dict:
        cur = financial_data.get("2025Q3", {})
        debt = cur.get("debt_ratio", 50)
        upside = valuation_result.get("upside_pct")
        risk_scores = {
            "经营风险": "中" if cur.get("net_margin", 0) < 10 else "低",
            "财务风险": "高" if debt > 80 else ("中" if debt > 65 else "低"),
            "行业风险": industry_data.get("industry_risk", "中"),
            "政策风险": industry_data.get("policy_risk", "中"),
            "估值风险": "低" if (upside or 0) > 10 else ("中" if (upside or 0) > 0 else "高"),
        }
        overall = _RISK_LABELS[max(_RISK_ORDER[v] for v in risk_scores.values())]
        return {"risk_scores": risk_scores, "overall_risk": overall,
                "risk_factors": industry_data.get("risk_notes", []),
                "assessed_by": "template-fallback"}

    fallback = _fallback()
    result = await _llm_json(
        "你是基金风控研究员，从财务、行业、政策、估值维度评估投资风险，只输出 JSON。",
        f"财务数据：{json.dumps(financial_data, ensure_ascii=False)}\n"
        f"行业信息：{json.dumps(industry_data, ensure_ascii=False)}\n"
        f"估值结果：{json.dumps(valuation_result, ensure_ascii=False)}\n\n"
        "评分锚点（务必参照，避免过度保守）：\n"
        "- 财务风险：资产负债率 <60% 低，60%-75% 中，75%-85% 中高，>85% 高\n"
        "- 经营风险：净利率 >15% 低，5%-15% 中，<5% 中高\n"
        "- 行业风险：红海竞争/产能过剩 中高，格局稳定的龙头 低\n"
        "- 政策风险：强监管行业 中高，一般行业 中\n"
        "- 估值风险：估值上行空间 >20% 低，0-20% 中，<0% 高\n\n"
        '请输出 JSON：{"risk_scores": {"经营风险": "低|中低|中|中高|高", "财务风险": "...", '
        '"行业风险": "...", "政策风险": "...", "估值风险": "..."}, '
        '"risk_factors": ["具体风险因素1", "风险因素2"]}',
        lambda: fallback)
    return _normalize_risk(result, fallback)


async def llm_extract_investment_logic(analysis_result: dict, market_context: dict) -> dict:
    """提炼投资逻辑（核心驱动因素/催化剂/预期差）。"""

    def _fallback() -> dict:
        report_analysis = analysis_result.get("report_analysis", {})
        return {
            "core_logic": report_analysis.get("logic", "暂无"),
            "catalysts": market_context.get("catalysts", ["季报业绩披露", "行业政策落地"]),
            "expected_gap": market_context.get(
                "expected_gap",
                f"综合估值 {analysis_result.get('valuation', {}).get('composite_value')} 元，"
                f"较现价 {analysis_result.get('valuation', {}).get('current_price')} 元存在预期差"),
            "extracted_by": "template-fallback",
        }

    return await _llm_json(
        "你是资深基金研究员，基于分析结果提炼投资逻辑，只输出 JSON。",
        f"分析结果：{json.dumps(analysis_result, ensure_ascii=False)}\n"
        f"市场背景：{json.dumps(market_context, ensure_ascii=False)}\n\n"
        '请输出 JSON：{"core_logic": "核心投资逻辑（100字内）", '
        '"catalysts": ["催化剂1", "催化剂2"], "expected_gap": "预期差描述（80字内）"}',
        _fallback)


# ---------------------------------------------------------------------------
# ReportAgent LLM 工具
# ---------------------------------------------------------------------------

_REPORT_SYSTEM = (
    "你是资深基金研究员，为基金公司撰写内部研究报告。"
    "要求：数据必须来自输入材料，不得编造；结构清晰、语言专业；"
    "必须包含【风险提示】和【免责声明】两个章节。"
)


async def llm_generate_report(analysis_result: dict, report_template: str) -> str:
    """生成基金研究报告正文（行业概况/核心逻辑/估值分析/风险提示/投资建议）。"""

    def _fallback() -> str:
        stock_name = analysis_result.get("stock_name", "标的")
        stock_code = analysis_result.get("stock_code", "")
        logic = analysis_result.get("investment_logic", {})
        valuation = analysis_result.get("valuation", {})
        indicators = analysis_result.get("financial_indicators", {}).get("indicators", {})
        risk = analysis_result.get("risk", {})
        rows = "\n".join(f"| {k} | {v} |" for k, v in indicators.items())
        return f"""## 一、核心结论

{stock_name}（{stock_code}）：评级 **{analysis_result.get('rating', '中性')}**，
综合目标价 **{valuation.get('composite_value')} 元**，较现价 {valuation.get('current_price')} 元
上行空间 {valuation.get('upside_pct')}%。

## 二、投资逻辑

{logic.get('core_logic', '暂无')}

## 三、财务概览

| 指标 | 2025Q3 |
| --- | --- |
{rows}

## 四、估值分析

综合目标价 {valuation.get('composite_value')} 元（PE {valuation.get('pe_value')} /
PS {valuation.get('ps_value')} / 成长 {valuation.get('growth_value')}）

## 五、风险提示

综合风险等级：{risk.get('overall_risk', '未知')}
主要风险因素：{'；'.join(risk.get('risk_factors', [])) or '暂无'}

## 六、免责声明

本报告由 FundAgent AI 辅助生成，仅供内部研究参考，不构成投资建议。市场有风险，投资需谨慎。
"""

    return await _llm_text(
        _REPORT_SYSTEM,
        f"报告模板：{report_template}\n"
        f"分析结果（JSON）：{json.dumps(analysis_result, ensure_ascii=False)}\n\n"
        "请按以下结构输出 Markdown 报告：一、核心结论（评级+目标价+上行空间）；"
        "二、投资逻辑；三、财务概览（表格）；四、估值分析（各方法结果+综合目标价）；"
        "五、风险提示（五维度评分表+主要风险因素）；六、免责声明。",
        _fallback, max_tokens=2500)


async def llm_generate_disclaimer(author_info: dict) -> str:
    """附加合规免责声明（固定文本，保证合规关键词稳定存在）。"""
    return (
        f"\n\n---\n**分析师声明**：{author_info.get('name', '')}"
        f"（基金从业资格证书编号：{author_info.get('qualification_no', 'A2025MOCK001')}）\n\n"
        "**利益冲突声明**：本分析师未持有本报告所涉标的，不存在利益冲突。\n\n"
        "**免责声明**：本报告由 FundAgent AI 辅助生成，数据来源于公司 Wind 数据终端，"
        "仅供内部研究参考，不构成投资建议。市场有风险，投资需谨慎。"
    )
