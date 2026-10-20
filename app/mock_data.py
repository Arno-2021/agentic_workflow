# -*- coding: utf-8 -*-
"""
FundAgent AI - Mock 数据层
================================
模拟公司 Wind 数据终端 / 基金报告库 / 巨潮资讯 / 央行统计数据 的返回值。
数据量刻意保持最小：6 只标的 + 少量宏观数据，足够跑通全流程 demo。

生产环境替换方式：
  - 外部 API 封装工具（api_tools.py）里把 Mock 数据源换成 httpx 调用公司 Wind 终端；
  - MCP 工具（mcp_tools.py）里把内存向量库换成真实 Milvus。
本文件只被 mock 模式读取，不影响 LangGraph 状态图结构。
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# 一、标的池（对应文档示例中的新能源主题 10 只里挑 6 只核心标的）
# ---------------------------------------------------------------------------

STOCKS: dict[str, dict] = {
    "300750": {
        "stock_code": "300750.SZ",
        "stock_name": "宁德时代",
        "market": "深交所创业板",
        "industry_l1": "电力设备",
        "industry_l2": "电池",
        "quote": {
            "latest_price": 285.00,
            "change_pct": 1.82,
            "volume": 32.5,            # 万手
            "amount": 92.3,            # 亿元
            "turnover_rt": 1.36,       # %
            "pe_ttm": 24.8,
            "pb": 4.1,
            "total_mv": 12560.0,       # 亿元
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=3250.0, net_profit=520.0, gross_margin=27.8,
                           net_margin=16.0, roe=15.2, debt_ratio=68.0, eps=11.8, ocf=680.0),
            "2024Q3": dict(revenue=2590.0, net_profit=405.0, gross_margin=26.5,
                           net_margin=15.6, roe=13.1, debt_ratio=70.2, eps=9.2, ocf=520.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-25",
                 summary="前三季度实现营业收入3250亿元（同比+25.5%），归母净利润520亿元（同比+28.4%），"
                         "动力电池销量 380GWh，全球市占率 37.2%。"),
            dict(title="关于建设海外储能电池生产基地的公告", ann_type="临时公告", ann_date="2025-09-12",
                 summary="拟投资不超过 80 亿元在匈牙利建设储能电池生产基地，达产后年产 40GWh。"),
        ],
        "research_reports": [
            dict(report_id="RPT-300750-001", title="宁德时代：动力电池龙头稳固，储能打造第二增长曲线",
                 source="公司研究所", publish_date="2025-10-28", rating="买入", target_price=380.0,
                 raw_text="公司动力电池全球市占率 37%，连续 5 年全球第一。储能业务收入占比提升至 15%，"
                          "全球储能市场 CAGR 40%+。麒麟电池能量密度 255Wh/kg，领先竞品一代；"
                          "钠离子电池预计 2026 年量产。风险：碳酸锂价格波动、行业竞争加剧、海外政策风险。"),
        ],
        "risk_notes": ["原材料碳酸锂价格波动（占成本 40%+）", "行业竞争加剧（比亚迪弗迪外供）", "美国 IRA 法案限制"],
    },
    "002594": {
        "stock_code": "002594.SZ",
        "stock_name": "比亚迪",
        "market": "深交所主板",
        "industry_l1": "汽车",
        "industry_l2": "乘用车",
        "quote": {
            "latest_price": 262.00,
            "change_pct": 0.95,
            "volume": 18.2,
            "amount": 47.8,
            "turnover_rt": 0.92,
            "pe_ttm": 21.5,
            "pb": 4.6,
            "total_mv": 7620.0,
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=5660.0, net_profit=298.0, gross_margin=20.1,
                           net_margin=5.3, roe=13.6, debt_ratio=74.5, eps=10.2, ocf=850.0),
            "2024Q3": dict(revenue=5020.0, net_profit=252.0, gross_margin=20.6,
                           net_margin=5.0, roe=12.4, debt_ratio=76.0, eps=8.7, ocf=610.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-30",
                 summary="前三季度营业收入 5660 亿元（同比+12.7%），新能源汽车销量 326 万辆（同比+38%）。"),
        ],
        "research_reports": [
            dict(report_id="RPT-002594-001", title="比亚迪：销量高增长，高端化与出海双击",
                 source="公司研究所", publish_date="2025-11-02", rating="买入", target_price=320.0,
                 raw_text="公司新能源汽车销量 326 万辆，同比+38%。高端品牌腾势/方程豹放量，出海销量 68 万辆。"
                          "垂直整合成本优势显著，智驾平权战略推进。风险：价格战加剧、海外关税政策。"),
        ],
        "risk_notes": ["行业价格战加剧", "海外关税与政策风险", "智驾投入加大影响利润"],
    },
    "600519": {
        "stock_code": "600519.SH",
        "stock_name": "贵州茅台",
        "market": "上交所主板",
        "industry_l1": "食品饮料",
        "industry_l2": "白酒",
        "quote": {
            "latest_price": 1520.00,
            "change_pct": -0.65,
            "volume": 3.2,
            "amount": 48.9,
            "turnover_rt": 0.21,
            "pe_ttm": 21.2,
            "pb": 8.3,
            "total_mv": 19100.0,
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=1050.0, net_profit=520.0, gross_margin=91.5,
                           net_margin=49.5, roe=32.8, debt_ratio=18.2, eps=41.5, ocf=480.0),
            "2024Q3": dict(revenue=920.0, net_profit=460.0, gross_margin=91.2,
                           net_margin=50.0, roe=31.5, debt_ratio=19.5, eps=36.7, ocf=450.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-24",
                 summary="前三季度营业收入 1050 亿元（同比+14.1%），归母净利润 520 亿元（同比+13.0%），"
                         "i茅台平台直销占比持续提升。"),
        ],
        "research_reports": [
            dict(report_id="RPT-600519-001", title="贵州茅台：经营稳健，直销体系持续完善",
                 source="公司研究所", publish_date="2025-10-26", rating="增持", target_price=1680.0,
                 raw_text="公司白酒龙头地位稳固，直营渠道占比提升带动吨价上行。预收账款充裕，"
                          "现金流优异。风险：白酒需求疲软、批价下行压力。"),
        ],
        "risk_notes": ["白酒终端需求疲软", "批价下行风险", "消费税政策调整预期"],
    },
    "601012": {
        "stock_code": "601012.SH",
        "stock_name": "隆基绿能",
        "market": "上交所主板",
        "industry_l1": "电力设备",
        "industry_l2": "光伏设备",
        "quote": {
            "latest_price": 18.60,
            "change_pct": 2.45,
            "volume": 156.0,
            "amount": 29.1,
            "turnover_rt": 2.05,
            "pe_ttm": None,          # 亏损公司，PE 不适用
            "pb": 1.4,
            "total_mv": 1410.0,
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=680.0, net_profit=-38.0, gross_margin=6.2,
                           net_margin=-5.6, roe=-3.8, debt_ratio=62.0, eps=-0.5, ocf=-52.0),
            "2024Q3": dict(revenue=760.0, net_profit=-72.0, gross_margin=4.1,
                           net_margin=-9.5, roe=-7.1, debt_ratio=66.5, eps=-0.95, ocf=-88.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-31",
                 summary="前三季度营业收入 680 亿元（同比-10.5%），亏损同比收窄，BC 电池产能爬坡顺利。"),
        ],
        "research_reports": [
            dict(report_id="RPT-601012-001", title="隆基绿能：BC 技术领先，行业底部孕育新机",
                 source="公司研究所", publish_date="2025-11-03", rating="增持", target_price=22.0,
                 raw_text="公司 BC 电池量产效率 26.5%，行业领先。硅片端亏损收窄，组件盈利有望触底回升。"
                          "风险：行业产能出清慢于预期、技术路线迭代。"),
        ],
        "risk_notes": ["光伏行业产能过剩出清缓慢", "技术路线迭代风险", "海外贸易壁垒"],
    },
    "300274": {
        "stock_code": "300274.SZ",
        "stock_name": "阳光电源",
        "market": "深交所创业板",
        "industry_l1": "电力设备",
        "industry_l2": "光伏设备",
        "quote": {
            "latest_price": 84.20,
            "change_pct": 3.12,
            "volume": 42.0,
            "amount": 35.6,
            "turnover_rt": 1.71,
            "pe_ttm": 15.8,
            "pb": 3.2,
            "total_mv": 1745.0,
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=610.0, net_profit=78.0, gross_margin=28.4,
                           net_margin=12.8, roe=16.9, debt_ratio=58.0, eps=3.8, ocf=95.0),
            "2024Q3": dict(revenue=495.0, net_profit=62.0, gross_margin=30.2,
                           net_margin=12.5, roe=14.8, debt_ratio=60.0, eps=3.0, ocf=72.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-26",
                 summary="前三季度营业收入 610 亿元（同比+23.2%），储能系统出货量全球前三。"),
        ],
        "research_reports": [
            dict(report_id="RPT-300274-001", title="阳光电源：光储双轮驱动，龙头盈利韧性足",
                 source="公司研究所", publish_date="2025-10-29", rating="增持", target_price=95.0,
                 raw_text="公司光伏逆变器全球市占率 30%+，储能系统出货量全球前三。海外大储需求爆发，"
                          "订单饱满。风险：汇率波动、海外政策不确定性。"),
        ],
        "risk_notes": ["汇率波动风险", "海外政策不确定性", "行业竞争加剧"],
    },
    "300014": {
        "stock_code": "300014.SZ",
        "stock_name": "亿纬锂能",
        "market": "深交所创业板",
        "industry_l1": "电力设备",
        "industry_l2": "电池",
        "quote": {
            "latest_price": 55.10,
            "change_pct": 1.20,
            "volume": 58.0,
            "amount": 32.0,
            "turnover_rt": 2.60,
            "pe_ttm": 28.5,
            "pb": 2.9,
            "total_mv": 1125.0,
            "trade_status": "正常交易",
        },
        "financials": {
            "2025Q3": dict(revenue=380.0, net_profit=32.0, gross_margin=17.5,
                           net_margin=8.4, roe=9.8, debt_ratio=63.0, eps=1.6, ocf=38.0),
            "2024Q3": dict(revenue=330.0, net_profit=28.0, gross_margin=17.0,
                           net_margin=8.5, roe=9.1, debt_ratio=65.0, eps=1.4, ocf=30.0),
        },
        "announcements": [
            dict(title="2025年第三季度报告", ann_type="定期报告", ann_date="2025-10-28",
                 summary="前三季度营业收入 380 亿元（同比+15.2%），动力电池储能电池出货稳步增长。"),
        ],
        "research_reports": [
            dict(report_id="RPT-300014-001", title="亿纬锂能：储能电池放量，盈利拐点渐近",
                 source="公司研究所", publish_date="2025-10-30", rating="中性", target_price=58.0,
                 raw_text="公司储能电池出货量进入全球前五，大圆柱电池量产在即。动力电池二线电池厂"
                          "盈利承压。风险：竞争加剧、原材料价格波动。"),
        ],
        "risk_notes": ["二线电池厂竞争激烈", "原材料价格波动", "大客户集中度较高"],
    },
}

# 主题 → 标的池（文档示例 4.4：新能源主题）
THEMES: dict[str, list[str]] = {
    "新能源": ["300750", "002594", "601012", "300274", "300014"],
    "白酒": ["600519"],
    "电池": ["300750", "300014"],
}

# ---------------------------------------------------------------------------
# 二、宏观数据（对应文档：央行统计数据 M2/GDP/CPI）
# ---------------------------------------------------------------------------

MACRO_DATA = {
    "as_of": "2025-09-30",
    "gdp": {"period": "2025Q3", "value": 319000.0, "yoy": 4.8, "unit": "亿元"},
    "cpi": {"period": "2025-09", "value": 0.4, "yoy": -0.3, "unit": "%"},
    "m2": {"period": "2025-09", "value": 3350000.0, "yoy": 6.8, "unit": "亿元"},
    "lpr_1y": 3.0,
    "lpr_5y": 3.5,
    "note": "央行发布的最新宏观金融数据（mock）",
}

# ---------------------------------------------------------------------------
# 三、行业分类（申万一级/二级，简化版关键词映射）
# ---------------------------------------------------------------------------

INDUSTRY_KEYWORDS = [
    (["电池", "锂电", "储能"], "电力设备", "电池"),
    (["光伏", "硅片", "组件", "逆变器"], "电力设备", "光伏设备"),
    (["白酒", "茅台", "酿酒"], "食品饮料", "白酒"),
    (["汽车", "整车", "乘用车"], "汽车", "乘用车"),
]

# ---------------------------------------------------------------------------
# 四、价格序列生成（确定性伪随机，用于风险指标计算，避免存储大量数据）
# ---------------------------------------------------------------------------


def _market_steps(days: int) -> list[float]:
    """市场共同因子日收益率序列（个股与基准共享，保证 Beta 为正且量级合理）。"""
    rng = random.Random(20250930)
    return [rng.gauss(0.0004, 0.006) for _ in range(days)]


def gen_price_series(stock_code: str, days: int = 120) -> list[float]:
    """根据股票代码确定性生成近 120 个交易日净值序列（mock 走势）。

    结构：市场共同因子 × 个股 Beta + 特异因子，使 Beta/相关性等风险指标更接近真实分布。
    """
    code = stock_code.split(".")[0]
    rng = random.Random(int(code))
    base = STOCKS[code]["quote"]["latest_price"]
    mkt = _market_steps(days)
    beta = 1.1 if STOCKS[code]["industry_l1"] in ("电力设备", "汽车") else 0.9
    prices = [base * 0.85]  # 起点：约 120 个交易日前
    for i in range(days - 1):
        idio = rng.gauss(0.0003, 0.011)     # 个股特异因子（日增量）
        prices.append(prices[-1] * (1 + beta * mkt[i] + idio))
    # 缩放使末值等于当前报价（保持时间轴与基准一致，Beta 为正）
    scale = base / prices[-1]
    return [round(p * scale, 2) for p in prices]


def gen_benchmark_series(days: int = 120) -> list[float]:
    """沪深300 基准序列（mock，与个股共享市场因子）。"""
    mkt = _market_steps(days)
    prices = [3950.0]
    for i in range(days - 1):
        prices.append(prices[-1] * (1 + mkt[i]))
    return [round(p, 2) for p in prices]


def trading_days(days: int = 120) -> list[str]:
    end = date(2025, 11, 7)
    out, d = [], end
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


# ---------------------------------------------------------------------------
# 五、预设研究员（对应文档 3.6 记忆机制 / 4.1 角色）
# ---------------------------------------------------------------------------

USERS = [
    dict(user_id="analyst_001", name="李文", role="analyst", department_id="research_dept_1",
         password="fund2026", title="行业研究员",
         preferences=dict(preferred_industries=["电力设备"], preferred_valuation_methods=["sharpe", "ps"],
                          risk_tolerance="moderate", report_style="深度型")),
    dict(user_id="fm_001", name="王总", role="fund_manager", department_id="research_dept_1",
         password="fund2026", title="基金经理"),
    dict(user_id="ic_001", name="陈委", role="committee", department_id="research_dept_1",
         password="fund2026", title="投委会成员"),
]


def find_stock(code_or_name: str) -> dict | None:
    """按代码（300750 / 300750.SZ）或名称（宁德时代）查找标的。"""
    key = code_or_name.strip()
    for s in STOCKS.values():
        if key in (s["stock_code"], s["stock_code"].split(".")[0], s["stock_name"]):
            return s
    return None


def find_stock_in_text(text: str) -> dict | None:
    """从自然语言中识别标的（名称或代码作为子串出现）。"""
    for s in STOCKS.values():
        if s["stock_name"] in text or s["stock_code"].split(".")[0] in text:
            return s
    return None


def search_stocks_by_keyword(keyword: str) -> list[dict]:
    """主题/行业关键词 → 标的列表。"""
    for theme, codes in THEMES.items():
        if theme in keyword:
            return [STOCKS[c] for c in codes]
    hit = find_stock(keyword)
    return [hit] if hit else list(STOCKS.values())
