# -*- coding: utf-8 -*-
"""
外部 API 封装（5+3 个）
=======================
文档 3.4.2：把公司业务系统 API 包装成 LangGraph 节点可调用的工具函数。
生产环境用 httpx 调公司 Wind 终端 / 基金报告库 / 投委会审批系统 / 合规管理系统；
本 demo 用 mock 数据源返回同样结构的数据，并模拟"字段映射"（公司字段 → LLM 友好字段）。

容错降级（文档 3.4.1 ④）：重试 1 次 → 切换备用源 → 友好提示。这里 mock 源不会失败，
但保留了 retry/degrade 的代码路径框架。
"""
from __future__ import annotations

import time
from typing import Any

from .. import mock_data
from ..db import store


class ToolError(Exception):
    """工具执行失败（携带友好提示，供节点降级处理）。"""


def _map_quote(raw: dict, stock: dict) -> dict:
    """字段映射：公司字段 → LLM 友好的中文字段（对应文档 3.4.2 ③）。"""
    q = raw
    return {
        "基金产品代码": stock["stock_code"],
        "基金产品名称": stock["stock_name"],
        "市场": stock["market"],
        "最新价": q["latest_price"],
        "涨跌幅": f"{q['change_pct']:+.2f}%",
        "成交量": f"{q['volume']}万手",
        "成交额": f"{q['amount']}亿元",
        "换手率": f"{q['turnover_rt']:.2f}%",
        "市盈率(TTM)": q.get("pe_ttm") if q.get("pe_ttm") is not None else "亏损不适用",
        "市净率": q["pb"],
        "总市值": f"{q['total_mv']:.0f}亿元",
        "交易状态": q["trade_status"],
        "更新时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


async def _with_retry(fn, retries: int = 1):
    """文档 3.4.1：外部 API 重试 1 次（网络抖动）。"""
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
    raise ToolError(f"数据终端暂时繁忙，请稍后再试（{last}）")


# ---------------------------------------------------------------------------
# 外部 API 封装工具
# ---------------------------------------------------------------------------

def get_market_quote(stock_code: str, quote_type: str = "realtime") -> dict:
    """查询实时净值数据（对接公司 Wind 数据终端）。"""
    stock = mock_data.find_stock(stock_code)
    if not stock:
        raise ToolError(f"未找到标的：{stock_code}")
    time.sleep(0.02)  # 模拟网络延迟
    return _map_quote(stock["quote"], stock)


def fetch_market_data_api(stock_codes: list[str], date_range: str = "120d") -> list[dict]:
    """批量采集市场净值数据（对接公司 Wind 数据终端）。"""
    out = []
    for code in stock_codes:
        stock = mock_data.find_stock(code)
        if not stock:
            continue
        out.append({
            "stock_code": stock["stock_code"],
            "stock_name": stock["stock_name"],
            "quote": _map_quote(stock["quote"], stock),
            "price_series": mock_data.gen_price_series(code),
            "dates": mock_data.trading_days(),
        })
    return out


def get_report_detail(report_id: str) -> dict:
    """查询基金报告详情（对接公司基金报告库）。"""
    for stock in mock_data.STOCKS.values():
        for rpt in stock["research_reports"]:
            if rpt["report_id"] == report_id:
                return {**rpt, "stock_code": stock["stock_code"], "stock_name": stock["stock_name"]}
    raise ToolError(f"未找到基金报告：{report_id}")


def save_research_result(department_id: str, user_id: str, thread_id: str, result_data: dict) -> dict:
    """保存分析结果到公司系统（Oracle）。"""
    record_id = store.save_analysis(department_id, user_id, thread_id, result_data)
    return {"success": True, "record_id": record_id}


def push_to_fund_manager(report_id: str, fund_manager_ids: list[str]) -> dict:
    """推送报告给基金经理（对接公司投委会审批系统）。"""
    return {"status": "pushed", "push_id": f"PUSH-{report_id}", "targets": fund_manager_ids}


def save_report_to_repository(department_id: str, user_id: str, thread_id: str,
                              title: str, body: str, report_type: str, compliance: dict) -> dict:
    """保存报告到基金报告库（Elasticsearch）。"""
    report_id = f"RPT-{int(time.time())}"
    store.save_report({
        "report_id": report_id, "department_id": department_id, "user_id": user_id,
        "thread_id": thread_id, "title": title, "report_type": report_type,
        "body": body, "compliance": compliance, "audit_status": "pending",
    })
    return {"success": True, "report_id": report_id}


def get_research_result(record_id: str, department_id: str, user_id: str) -> dict:
    """读取已保存的分析结果（跨 Agent 上下文传递的落库读取）。"""
    rows = store.list_analysis(department_id, user_id, limit=50)
    for r in rows:
        if r["id"] == record_id:
            import json
            return json.loads(r["payload"])
    raise ToolError(f"未找到分析结果：{record_id}")


def save_audit_log(department_id: str, user_id: str, action: str, detail: Any) -> dict:
    """记录审计日志（留痕 5 年）。"""
    store.log(action, department_id, user_id, detail)
    return {"success": True}
