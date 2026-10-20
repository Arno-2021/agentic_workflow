# -*- coding: utf-8 -*-
"""
持久化层（Oracle-like Store）
============================
文档中的生产存储是 Oracle，这里用 SQLite 落地（零依赖、可直接跑），
表结构、字段、隔离策略与文档 3.6 节保持一致：

  - 所有业务表带 department_id + user_id，查询强制 WHERE 隔离（信息隔离墙）
  - analyst_preferences      研究员偏好（长期记忆）
  - stock_tracking_history   单只基金跟踪历史（长期记忆，永久保留）
  - report_audit_records     基金报告审核记录（保留 5 年，证监会合规）
  - analysis_results         分析结果
  - reports                  生成的基金报告
  - audit_logs               审计留痕（保留 5 年）
  - chat_sessions/messages   会话与消息（90 天归档策略由 retention 配置表达）

生产替换：把 sqlite3 连接换成 oracledb 连接池即可，DAO 方法签名不变。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime
from typing import Any

from .config import settings
from .dbutil import get_conn, locked_methods

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyst_preferences (
    id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    preferred_industries TEXT,          -- JSON array
    preferred_valuation_methods TEXT,   -- JSON array
    risk_tolerance TEXT,
    report_style TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_tracking_history (
    id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    industry_l1 TEXT,
    industry_l2 TEXT,
    latest_rating TEXT,
    latest_target_price REAL,
    valuation_records TEXT,             -- JSON
    risk_assessments TEXT,              -- JSON
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS report_audit_records (
    id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    reviewer_id TEXT,
    report_title TEXT,
    report_type TEXT,
    compliance_result TEXT,
    reviewer_comments TEXT,
    audit_status TEXT,                  -- pending/approved/rejected
    audit_date TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS analysis_results (
    id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    stock_code TEXT,
    stock_name TEXT,
    rating TEXT,
    target_price REAL,
    risk_level TEXT,
    payload TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    title TEXT,
    report_type TEXT,
    body TEXT,
    compliance TEXT,
    audit_status TEXT DEFAULT 'pending',
    reviewer_id TEXT,
    reviewer_comments TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    department_id TEXT,
    user_id TEXT,
    action TEXT,
    detail TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    thread_id TEXT PRIMARY KEY,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    department_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT,
    content TEXT,
    created_at TEXT
);
"""


def _connect() -> sqlite3.Connection:
    return get_conn()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def init_db(seed_users: bool = True) -> None:
    conn = _connect()
    conn.executescript(_SCHEMA)
    conn.commit()


@locked_methods
class OracleLikeStore:
    """所有查询强制 department_id/user_id 隔离（对应文档 3.6 信息隔离墙）。"""

    # ---------------- 研究员偏好 ----------------
    def get_preferences(self, department_id: str, user_id: str) -> dict:
        row = _connect().execute(
            "SELECT * FROM analyst_preferences WHERE department_id=? AND user_id=?",
            (department_id, user_id)).fetchone()
        if not row:
            return {}
        return {
            "preferred_industries": json.loads(row["preferred_industries"] or "[]"),
            "preferred_valuation_methods": json.loads(row["preferred_valuation_methods"] or "[]"),
            "risk_tolerance": row["risk_tolerance"] or "moderate",
            "report_style": row["report_style"] or "标准型",
        }

    def upsert_preferences(self, department_id: str, user_id: str, prefs: dict) -> None:
        conn = _connect()
        old = self.get_preferences(department_id, user_id)
        merged = {**old, **prefs}
        conn.execute(
            "INSERT INTO analyst_preferences(id,department_id,user_id,preferred_industries,"
            "preferred_valuation_methods,risk_tolerance,report_style,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET preferred_industries=excluded.preferred_industries,"
            " preferred_valuation_methods=excluded.preferred_valuation_methods,"
            " risk_tolerance=excluded.risk_tolerance, report_style=excluded.report_style,"
            " updated_at=excluded.updated_at",
            (_uid(), department_id, user_id,
             json.dumps(merged.get("preferred_industries", []), ensure_ascii=False),
             json.dumps(merged.get("preferred_valuation_methods", []), ensure_ascii=False),
             merged.get("risk_tolerance", "moderate"), merged.get("report_style", "标准型"), _now()))
        conn.commit()

    # ---------------- 单只基金跟踪历史 ----------------
    def upsert_tracking(self, department_id: str, user_id: str, record: dict) -> None:
        conn = _connect()
        exist = conn.execute(
            "SELECT id FROM stock_tracking_history WHERE department_id=? AND user_id=? AND stock_code=?",
            (department_id, user_id, record["stock_code"])).fetchone()
        if exist:
            conn.execute(
                "UPDATE stock_tracking_history SET latest_rating=?, latest_target_price=?,"
                " valuation_records=?, risk_assessments=?, updated_at=? WHERE id=?",
                (record.get("rating"), record.get("target_price"),
                 json.dumps(record.get("valuation", {}), ensure_ascii=False),
                 json.dumps(record.get("risk", {}), ensure_ascii=False), _now(), exist["id"]))
        else:
            conn.execute(
                "INSERT INTO stock_tracking_history(id,department_id,user_id,stock_code,stock_name,"
                "industry_l1,industry_l2,latest_rating,latest_target_price,valuation_records,"
                "risk_assessments,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), department_id, user_id, record["stock_code"], record.get("stock_name"),
                 record.get("industry_l1"), record.get("industry_l2"), record.get("rating"),
                 record.get("target_price"),
                 json.dumps(record.get("valuation", {}), ensure_ascii=False),
                 json.dumps(record.get("risk", {}), ensure_ascii=False), _now()))
        conn.commit()

    def list_tracking(self, department_id: str, user_id: str) -> list[dict]:
        rows = _connect().execute(
            "SELECT * FROM stock_tracking_history WHERE department_id=? AND user_id=?"
            " ORDER BY updated_at DESC", (department_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 分析结果 ----------------
    def save_analysis(self, department_id: str, user_id: str, thread_id: str, result: dict) -> str:
        conn = _connect()
        rid = _uid()
        conn.execute(
            "INSERT INTO analysis_results(id,department_id,user_id,thread_id,stock_code,stock_name,"
            "rating,target_price,risk_level,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rid, department_id, user_id, thread_id, result.get("stock_code"),
             result.get("stock_name"), result.get("rating"), result.get("target_price"),
             result.get("risk_level"), json.dumps(result, ensure_ascii=False), _now()))
        conn.commit()
        return rid

    def list_analysis(self, department_id: str, user_id: str, limit: int = 20) -> list[dict]:
        rows = _connect().execute(
            "SELECT * FROM analysis_results WHERE department_id=? AND user_id=?"
            " ORDER BY created_at DESC LIMIT ?", (department_id, user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 基金报告 ----------------
    def save_report(self, report: dict) -> None:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO reports(report_id,department_id,user_id,thread_id,title,"
            "report_type,body,compliance,audit_status,reviewer_id,reviewer_comments,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (report["report_id"], report["department_id"], report["user_id"],
             report.get("thread_id"), report.get("title"), report.get("report_type", "行业报告"),
             report.get("body", ""), json.dumps(report.get("compliance", {}), ensure_ascii=False),
             report.get("audit_status", "pending"), report.get("reviewer_id"),
             report.get("reviewer_comments"), _now()))
        conn.commit()

    def list_reports(self, department_id: str, user_id: str | None = None) -> list[dict]:
        conn = _connect()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM reports WHERE department_id=? AND user_id=? ORDER BY created_at DESC",
                (department_id, user_id)).fetchall()
        else:  # 投委会视角：部门内全部
            rows = conn.execute(
                "SELECT * FROM reports WHERE department_id=? ORDER BY created_at DESC",
                (department_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_report(self, report_id: str) -> dict | None:
        row = _connect().execute("SELECT * FROM reports WHERE report_id=?", (report_id,)).fetchone()
        return dict(row) if row else None

    def audit_report(self, report_id: str, reviewer_id: str, decision: str, comments: str) -> None:
        conn = _connect()
        status = {"approve": "approved", "reject": "rejected"}.get(decision, decision)
        now = _now()
        conn.execute(
            "UPDATE reports SET audit_status=?, reviewer_id=?, reviewer_comments=? WHERE report_id=?",
            (status, reviewer_id, comments, report_id))
        conn.execute(
            "INSERT INTO report_audit_records(id,department_id,user_id,report_id,reviewer_id,"
            "report_title,report_type,compliance_result,reviewer_comments,audit_status,audit_date,created_at)"
            " SELECT ?,department_id,user_id,report_id,?,title,report_type,compliance,?,?,?,? FROM reports"
            " WHERE report_id=?",
            (_uid(), reviewer_id, comments, status, now, now, report_id))
        conn.commit()

    # ---------------- 审计日志 ----------------
    def log(self, action: str, department_id: str = "", user_id: str = "", detail: Any = None) -> None:
        conn = _connect()
        conn.execute(
            "INSERT INTO audit_logs(id,department_id,user_id,action,detail,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (_uid(), department_id, user_id, action,
             json.dumps(detail, ensure_ascii=False) if detail is not None else "", _now()))
        conn.commit()

    def list_logs(self, department_id: str, limit: int = 50) -> list[dict]:
        rows = _connect().execute(
            "SELECT * FROM audit_logs WHERE department_id=? ORDER BY created_at DESC LIMIT ?",
            (department_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 会话与消息 ----------------
    def create_session(self, thread_id: str, department_id: str, user_id: str, title: str) -> None:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO chat_sessions(thread_id,department_id,user_id,title,status,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (thread_id, department_id, user_id, title, "active", _now(), _now()))
        conn.commit()

    def touch_session(self, thread_id: str, status: str | None = None) -> None:
        conn = _connect()
        if status:
            conn.execute("UPDATE chat_sessions SET updated_at=?, status=? WHERE thread_id=?",
                         (_now(), status, thread_id))
        else:
            conn.execute("UPDATE chat_sessions SET updated_at=? WHERE thread_id=?", (_now(), thread_id))
        conn.commit()

    def list_sessions(self, department_id: str, user_id: str) -> list[dict]:
        rows = _connect().execute(
            "SELECT * FROM chat_sessions WHERE department_id=? AND user_id=?"
            " ORDER BY updated_at DESC LIMIT 100", (department_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    def save_message(self, thread_id: str, department_id: str, user_id: str, role: str, content: str) -> None:
        conn = _connect()
        conn.execute(
            "INSERT INTO chat_messages(id,thread_id,department_id,user_id,role,content,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (_uid(), thread_id, department_id, user_id, role, content, _now()))
        conn.commit()

    def list_messages(self, thread_id: str) -> list[dict]:
        rows = _connect().execute(
            "SELECT * FROM chat_messages WHERE thread_id=? ORDER BY created_at", (thread_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 看板统计 ----------------
    def dashboard(self, department_id: str) -> dict:
        conn = _connect()
        def _count(sql: str, args: tuple) -> int:
            return conn.execute(sql, args).fetchone()[0]
        return {
            "reports_total": _count("SELECT COUNT(*) FROM reports WHERE department_id=?", (department_id,)),
            "reports_pending": _count(
                "SELECT COUNT(*) FROM reports WHERE department_id=? AND audit_status='pending'", (department_id,)),
            "analysis_total": _count("SELECT COUNT(*) FROM analysis_results WHERE department_id=?", (department_id,)),
            "tracking_total": _count(
                "SELECT COUNT(*) FROM stock_tracking_history WHERE department_id=?", (department_id,)),
            "tasks_total": _count("SELECT COUNT(*) FROM chat_sessions WHERE department_id=?", (department_id,)),
        }


store = OracleLikeStore()
