# -*- coding: utf-8 -*-
"""
SQLite 连接工具（被 db.py 与 graph/checkpointer.py 共用）
=========================================================
- 生产对应 Oracle 连接池；demo 用单文件 SQLite，单连接 + 线程锁保证并发安全。
- 自动降级：WAL 模式不可用时（如某些沙箱环境会拦截 journal 文件），
  自动重建连接并切换 journal_mode=MEMORY + locking_mode=EXCLUSIVE。
"""
from __future__ import annotations

import asyncio
import functools
import sqlite3
import threading

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
# SQLite 单连接跨线程安全锁：所有 execute/commit 都必须持有该锁，
# 否则多线程（langgraph 线程池 + FastAPI）会竞争同一连接导致
# "cannot commit - no transaction is active"。
tx_lock = threading.RLock()


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS _probe(x)")
        conn.execute("DROP TABLE _probe")
        conn.commit()
    except sqlite3.OperationalError:
        # 当前环境 journal 文件不可用（如沙箱）→ 重建连接走内存 journal
        conn.close()
        conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    return conn


def get_conn() -> sqlite3.Connection:
    """全局单例连接（RLock 保护，跨线程安全）。"""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _make_conn()
        return _conn


def locked_methods(cls):
    """类装饰器：给所有公开【同步】方法加 tx_lock，保证 SQLite 单连接的线程安全。

    注意：不要装饰 async 方法——它们内部通过 asyncio.to_thread 调用同步方法，
    同步方法自己会加锁；若异步方法也持锁，会与 worker 线程形成死锁。
    """
    for name, member in list(vars(cls).items()):
        if name.startswith("__") or not callable(member):
            continue
        if asyncio.iscoroutinefunction(member):
            continue

        @functools.wraps(member)
        def wrapper(*args, __fn=member, **kwargs):
            with tx_lock:
                return __fn(*args, **kwargs)
        setattr(cls, name, wrapper)
    return cls
