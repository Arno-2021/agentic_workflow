# -*- coding: utf-8 -*-
"""
MCP 工具（4 个）
================
文档 3.4：通过 MCP（FastMCP）协议对接外部知识服务。
  - get_fund_report        从 Wind 数据终端获取财务报表
  - query_announcements    查询基金公司公告（巨潮资讯）
  - store_to_vector_db     结构化数据存入 Milvus 向量库
  - search_research_reports 基金报告库检索（向量 + 关键词）

mock 模式：Milvus 用内存向量库（关键词 + 字符 n-gram 相似度）替代，
存储与检索接口与生产一致。
"""
from __future__ import annotations

import math
from typing import Any

from .. import mock_data

# ---------------------------------------------------------------------------
# 内存向量库（mock Milvus）：存储结构化数据 + 简单文本向量
# ---------------------------------------------------------------------------

_VECTOR_STORE: dict[str, dict] = {}


def _text_vector(text: str) -> dict[str, float]:
    """字符级 n-gram 词袋（mock embedding，确定性）。"""
    vec: dict[str, float] = {}
    text = (text or "").lower()
    for n in (2, 3):
        for i in range(len(text) - n + 1):
            vec[text[i:i + n]] = vec.get(text[i:i + n], 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    common = set(a) & set(b)
    return sum(a[k] * b[k] for k in common)


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------

def get_fund_report(stock_code: str, report_type: str = "定期报告", period: str = "2025Q3") -> dict:
    """从 Wind 数据终端获取基金公司财务报表（资产负债表/利润表/现金流量表摘要）。"""
    stock = mock_data.find_stock(stock_code)
    if not stock:
        return {"success": False, "message": f"未找到标的：{stock_code}"}
    fin = stock["financials"].get(period)
    if not fin:
        return {"success": False, "message": f"无 {period} 财务数据"}
    return {
        "success": True,
        "stock_code": stock["stock_code"],
        "stock_name": stock["stock_name"],
        "period": period,
        "report_type": report_type,
        # 模拟三张表的核心字段
        "income_statement": {"营业收入": fin["revenue"], "净利润": fin["net_profit"],
                             "毛利率": fin["gross_margin"], "净利率": fin["net_margin"],
                             "EPS": fin["eps"]},
        "balance_sheet": {"资产负债率": fin["debt_ratio"]},
        "cash_flow": {"经营现金流净额": fin["ocf"]},
        "source": "Wind 数据终端（mock）",
    }


def query_announcements(stock_code: str, announcement_type: str = "",
                        start_date: str = "", end_date: str = "") -> list[dict]:
    """查询基金公司基金公告（年度报告/季度报告/临时基金公告）。"""
    stock = mock_data.find_stock(stock_code)
    if not stock:
        return []
    anns = stock["announcements"]
    if announcement_type:
        anns = [a for a in anns if announcement_type in a["ann_type"] or a["ann_type"] in announcement_type]
    if start_date:
        anns = [a for a in anns if a["ann_date"] >= start_date]
    if end_date:
        anns = [a for a in anns if a["ann_date"] <= end_date]
    return [dict(a, stock_code=stock["stock_code"], stock_name=stock["stock_name"]) for a in anns]


def store_to_vector_db(data: dict, category: str, embedding_model: str = "mock-bge") -> dict:
    """将结构化数据存储到 Milvus 向量数据库。"""
    vector_id = f"{category}_{data.get('stock_code', 'na')}_{len(_VECTOR_STORE)}"
    text = data.get("_text", "")
    _VECTOR_STORE[vector_id] = {
        "data": {k: v for k, v in data.items() if not k.startswith("_")},
        "category": category,
        "embedding_model": embedding_model,
        "vector": _text_vector(text),
        "text": text,
    }
    return {"success": True, "vector_id": vector_id, "category": category}


def search_research_reports(query: str, stock_code: str = "", top_k: int = 3,
                            filters: dict | None = None) -> list[dict]:
    """在基金报告库中检索相关基金报告（向量相似度 + 标的过滤）。"""
    qv = _text_vector(query)
    hits = []
    for stock in mock_data.STOCKS.values():
        if stock_code and stock_code not in (stock["stock_code"], stock["stock_code"].split(".")[0]):
            continue
        for rpt in stock["research_reports"]:
            score = _cosine(qv, _text_vector(rpt["raw_text"] + rpt["title"]))
            hits.append({**rpt, "stock_code": stock["stock_code"],
                         "stock_name": stock["stock_name"], "score": round(score, 4)})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]
