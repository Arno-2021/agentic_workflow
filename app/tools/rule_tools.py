# -*- coding: utf-8 -*-
"""
规则引擎工具（9 个）
===================
文档 3.4：财务指标计算、业绩归因模型、风险指标、数据一致性、合规审查——
纯数学/结构性检查，用代码算"又快又准"（LLM 反而慢且可能算错）。
"""
from __future__ import annotations

import math
from typing import Any

from .. import mock_data
from ..config import settings

# 无风险利率（年度），用于夏普比率
RISK_FREE_RATE = 0.02


# ---------------------------------------------------------------------------
# DataAgent 用规则工具
# ---------------------------------------------------------------------------

def format_data_standard(raw_data: dict, source: str) -> dict:
    """将原始净值数据格式化为统一标准（时间格式/价格单位/市值单位）。"""
    q = dict(raw_data)
    if "latest_price" in q:
        q["latest_price"] = round(float(q["latest_price"]), 2)
    if "total_mv" in q:
        q["total_mv"] = round(float(q["total_mv"]), 2)
    q["_source"] = source
    q["_standard"] = "price:元, mv:亿元, pct:%"
    return q


def align_financial_data(financial_data_multi_source: list[dict]) -> dict:
    """
    对齐多源财务数据口径（合并报表 vs 母公司报表、调整前 vs 调整后）。
    mock 场景：Wind/公告/第三方三源同结构，取中位数并对差异打标。
    """
    if not financial_data_multi_source:
        return {}
    keys = financial_data_multi_source[0].keys()
    aligned: dict[str, Any] = {}
    for k in keys:
        vals = [float(d[k]) for d in financial_data_multi_source
                if isinstance(d.get(k), (int, float))]
        if vals:
            vals.sort()
            aligned[k] = round(vals[len(vals) // 2], 4)  # 中位数对齐
    aligned["_aligned_from"] = len(financial_data_multi_source)
    return aligned


def industry_classify(stock_code: str, stock_name: str, business_desc: str = "") -> dict:
    """按申万一级/二级行业分类标准对标的进行行业归类。"""
    text = f"{stock_name}{business_desc}"
    for keywords, l1, l2 in mock_data.INDUSTRY_KEYWORDS:
        if any(k in text for k in keywords):
            return {"industry_l1": l1, "industry_l2": l2, "classification": "申万行业分类(模拟)"}
    return {"industry_l1": "未知", "industry_l2": "未知", "classification": "未匹配"}


def data_consistency_check(data_multi_source: dict) -> dict:
    """
    多源数据一致性校验（Wind vs Bloomberg vs 基金公告交叉比对）。
    文档 Q2：差异 < 1% 取权威源；> 1% 标记异常要求人工确认。
    """
    discrepancies, checks = [], []
    for field, sources in data_multi_source.items():
        if not isinstance(sources, dict) or len(sources) < 2:
            continue
        vals = {src: float(v) for src, v in sources.items() if isinstance(v, (int, float))}
        if len(vals) < 2:
            continue
        base = vals.get("wind") or list(vals.values())[0]
        for src, v in vals.items():
            if base and abs(v - base) / abs(base) > settings.consistency_threshold:
                discrepancies.append({"field": field, "source": src,
                                      "diff_pct": round(abs(v - base) / abs(base) * 100, 2)})
            checks.append(f"{field}[{src}]={v}")
    return {
        "is_consistent": not discrepancies,
        "discrepancies": discrepancies,
        "confidence": 1.0 if not discrepancies else max(0.5, 1 - 0.1 * len(discrepancies)),
        "authoritative_source": "wind",
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# AnalysisAgent 用规则工具
# ---------------------------------------------------------------------------

def financial_indicator_calc(financial_statements: dict, indicator_list: list[str]) -> dict:
    """计算核心财务指标（ROE/ROA/毛利率/净利率/资产负债率/周转率等）。"""
    cur = financial_statements.get("2025Q3", {})
    prev = financial_statements.get("2024Q3", {})
    indicators, yoy = {}, {}
    def _yoy(name: str) -> str:
        if name in cur and name in prev and prev[name] != 0:
            return f"{(cur[name] - prev[name]) / abs(prev[name]) * 100:+.1f}%"
        return "-"
    for ind in indicator_list:
        if ind == "revenue":
            indicators["营业收入(亿元)"] = cur.get("revenue")
            yoy["营业收入"] = _yoy("revenue")
        elif ind == "net_profit":
            indicators["净利润(亿元)"] = cur.get("net_profit")
            yoy["净利润"] = _yoy("net_profit")
        elif ind == "gross_margin":
            indicators["毛利率(%)"] = cur.get("gross_margin")
            yoy["毛利率(pct)"] = f"{cur.get('gross_margin', 0) - prev.get('gross_margin', 0):+.1f}"
        elif ind == "net_margin":
            indicators["净利率(%)"] = cur.get("net_margin")
            yoy["净利率(pct)"] = f"{cur.get('net_margin', 0) - prev.get('net_margin', 0):+.1f}"
        elif ind == "roe":
            indicators["ROE(%)"] = cur.get("roe")
            yoy["ROE(pct)"] = f"{cur.get('roe', 0) - prev.get('roe', 0):+.1f}"
        elif ind == "debt_ratio":
            indicators["资产负债率(%)"] = cur.get("debt_ratio")
            yoy["资产负债率(pct)"] = f"{cur.get('debt_ratio', 0) - prev.get('debt_ratio', 0):+.1f}"
        elif ind == "eps":
            indicators["EPS(元)"] = cur.get("eps")
            yoy["EPS"] = _yoy("eps")
    return {"indicators": indicators, "yoy_changes": yoy, "period": "2025Q3"}


def _annualize(periods_per_year: int = 4) -> float:
    return math.sqrt(periods_per_year)


def risk_indicator_calc(price_series: list[float], benchmark_series: list[float]) -> dict:
    """
    计算风险指标：Beta / 最大回撤 / 夏普比率 / 波动率 / VaR(95%)。
    文档 3.3.2：夏普比率、信息比率、最大回撤等多方法风险评估。
    """
    n = min(len(price_series), len(benchmark_series))
    px, bm = price_series[-n:], benchmark_series[-n:]
    rets = [(px[i] - px[i - 1]) / px[i - 1] for i in range(1, n)]
    bm_rets = [(bm[i] - bm[i - 1]) / bm[i - 1] for i in range(1, n)]

    mean_r = sum(rets) / len(rets)
    var_r = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
    std_r = math.sqrt(var_r)
    cov = sum((rets[i] - mean_r) * (bm_rets[i] - sum(bm_rets) / len(bm_rets))
              for i in range(len(rets))) / max(1, len(rets) - 1)
    var_bm = sum((b - sum(bm_rets) / len(bm_rets)) ** 2 for b in bm_rets) / max(1, len(bm_rets) - 1)
    beta = cov / var_bm if var_bm else 1.0

    # 最大回撤
    peak, mdd = px[0], 0.0
    for p in px:
        peak = max(peak, p)
        mdd = min(mdd, (p - peak) / peak)

    sharpe = (mean_r * 252 - RISK_FREE_RATE) / (std_r * math.sqrt(252)) if std_r else 0.0
    vol = std_r * math.sqrt(252)
    # 信息比率（相对基准超额收益的稳定性）
    excess = [rets[i] - bm_rets[i] for i in range(len(rets))]
    mean_e = sum(excess) / len(excess)
    te = math.sqrt(sum((e - mean_e) ** 2 for e in excess) / max(1, len(excess) - 1))
    info_ratio = mean_e * 252 / (te * math.sqrt(252)) if te else 0.0
    # VaR 95%（历史模拟法）
    sorted_rets = sorted(rets)
    var_95 = sorted_rets[max(0, int(0.05 * len(sorted_rets)))]

    return {
        "beta": round(beta, 2),
        "max_drawdown": round(mdd * 100, 2),          # %
        "sharpe": round(sharpe, 2),
        "information_ratio": round(info_ratio, 2),
        "volatility": round(vol * 100, 2),            # %
        "var_95": round(var_95 * 100, 2),             # %
        "annual_return": round(mean_r * 252 * 100, 2),
    }


def valuation_model_calc(financial_data: dict, market_data: dict,
                         valuation_methods: list[str]) -> dict:
    """
    构建业绩归因模型（多方法综合估值）。
    文档 Q3：单一估值方法有盲区，多方法加权更稳健。
      - PE 估值：target_pe × EPS(TTM)
      - PS 估值：target_ps × 每股营收
      - 成长估值：基于营收 CAGR 的风险调整估值
    权重按行业特性分配（成长股重 PE/PS，消费股重 PE/成长）。
    """
    cur = financial_data.get("2025Q3", {})
    prev = financial_data.get("2024Q3", {})
    price = market_data.get("latest_price", 0) or 0
    eps = cur.get("eps", 0) or 0
    revenue = cur.get("revenue", 0) or 0
    # 估算股本（市值/价格）
    shares = (market_data.get("total_mv", 0) * 1e8 / price) if price else 1.0
    rps = revenue * 1e8 / shares if shares else 0.0  # 每股营收
    growth = ((revenue - prev.get("revenue", revenue)) / prev.get("revenue", revenue)
              if prev.get("revenue") else 0.0)

    industry = market_data.get("industry_l1", "")
    if industry in ("食品饮料",):
        weights = {"pe": 0.5, "ps": 0.1, "growth": 0.4}
        target_pe, target_ps = 25.0, 8.0
    elif market_data.get("pe_ttm") is None:  # 亏损公司 → PS/成长为主
        weights = {"pe": 0.0, "ps": 0.5, "growth": 0.5}
        target_pe, target_ps = 0.0, 3.0
    else:  # 成长行业
        weights = {"pe": 0.4, "ps": 0.3, "growth": 0.3}
        target_pe, target_ps = 30.0, 4.0

    pe_value = round(target_pe * eps, 2) if weights["pe"] else None
    ps_value = round(target_ps * rps, 2) if weights["ps"] else None
    # 成长估值：当前价按营收 CAGR 折现 3 年（mock 简化）
    cagr = max(0.05, growth)
    growth_value = round(price * (1 + cagr) ** 3 / (1 + 0.09) ** 3, 2)

    composite = round(
        (pe_value or 0) * weights["pe"] + (ps_value or 0) * weights["ps"]
        + growth_value * weights["growth"], 2)

    return {
        "pe_value": pe_value,
        "ps_value": ps_value,
        "growth_value": growth_value,
        "composite_value": composite,
        "weights": weights,
        "target_pe": target_pe, "target_ps": target_ps,
        "current_price": price,
        "upside_pct": round((composite - price) / price * 100, 1) if price else None,
        "method_note": "多方法加权综合估值（文档 Q3：比单一方法稳健）",
    }


# ---------------------------------------------------------------------------
# ReportAgent 用规则工具
# ---------------------------------------------------------------------------

# 合规关键词（文档 3.3.3 compliance_check / 3.8 Q4）
_COMPLIANCE_RULES = {
    "信息隔离墙": ["未公开重大信息", "墙内信息", "调研纪要独家"],
    "利益冲突": ["我司自营重仓", "利益输送"],
    "免责声明": [],
    "荐股表述": ["保证收益", "稳赚不赔", "零风险"],
}


def compliance_filter(report_text: str, author_info: dict) -> dict:
    """
    合规审查：信息隔离墙 / 利益冲突 / 免责声明缺失 / 违规荐股表述。
    发现违规自动拦截，留痕备查（文档 3.8 Q4）。
    """
    violations, missing_items = [], []
    for rule, keywords in _COMPLIANCE_RULES.items():
        if rule == "免责声明":
            # 措辞等价匹配：LLM 生成的报告不会固定写"免责声明"四个字
            has_risk = "风险提示" in report_text or "风险" in report_text
            has_disclaimer = "免责声明" in report_text or "不构成投资建议" in report_text
            if not (has_risk and has_disclaimer):
                missing_items.append("免责声明/风险提示")
            continue
        for kw in keywords:
            if kw in report_text:
                violations.append({"rule": rule, "keyword": kw})
    # 作者资质检查（基金从业资格）
    if not author_info.get("qualification_no"):
        missing_items.append("基金从业资格证书编号")
    return {
        "is_compliant": not violations and not missing_items,
        "violations": violations,
        "missing_items": missing_items,
        "checked_at": time_str(),
    }


def time_str() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def format_report_template(report_body: str, template_id: str = "default") -> str:
    """按公司基金报告模板格式化（标题/摘要/正文/图表/免责声明）。"""
    header = "=" * 62 + "\n【FundAgent AI · 基金研究报告】（模板: %s）\n" % template_id + "=" * 62 + "\n"
    footer = "\n" + "-" * 62 + "\n本报告由 FundAgent AI 辅助生成，数据来源于公司 Wind 数据终端（mock），\n" \
                            "仅供内部研究参考，不构成投资建议。投资有风险，决策需谨慎。\n" + "-" * 62
    return header + report_body + footer
