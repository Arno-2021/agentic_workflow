# -*- coding: utf-8 -*-
"""
SQLite Checkpointer（对应文档的"自定义 Oracle Checkpointer"）
============================================================
文档 3.6：Checkpointer 按 thread_id 隔离 State，生产后端是 Oracle。
本 demo 用 SQLite 实现同一套 BaseCheckpointSaver 接口，
生产替换只需把存储后端换成 Oracle（DAO 不变）。

能力：多轮对话记忆（同 thread_id 自动加载 State）、断点续传（HitL interrupt 恢复）。
"""
from __future__ import annotations

import asyncio
import pickle
import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ..dbutil import get_conn, locked_methods

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lg_checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    checkpoint BLOB NOT NULL,
    metadata BLOB NOT NULL,
    created_at TEXT,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS lg_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""

_local = threading.local()


def _conn() -> sqlite3.Connection:
    return get_conn()


# 模块加载时确保表存在
get_conn().executescript(_SCHEMA)
get_conn().commit()


@locked_methods
class SqliteCheckpointSaver(BaseCheckpointSaver):
    """基于 SQLite 的 LangGraph Checkpointer（接口对齐 Oracle 版）。"""

    serde: SerializerProtocol = JsonPlusSerializer()

    # ---- 写 ----
    def put(self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata, new_versions: dict) -> RunnableConfig:
        conf = config.get("configurable", {})
        thread_id = conf["thread_id"]
        ns = conf.get("checkpoint_ns", "")
        blob = pickle.dumps((checkpoint, metadata))
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO lg_checkpoints(thread_id,checkpoint_ns,checkpoint_id,"
            "parent_checkpoint_id,checkpoint,metadata,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            (thread_id, ns, checkpoint["id"], checkpoint.get("parent_checkpoint_id", ""),
             blob, b"", ))
        conn.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple],
                   task_id: str, task_path: str = "") -> None:
        conf = config.get("configurable", {})
        thread_id, ns, ckpt_id = conf["thread_id"], conf.get("checkpoint_ns", ""), conf["checkpoint_id"]
        conn = _conn()
        for idx, (channel, value) in enumerate(writes):
            conn.execute(
                "INSERT OR REPLACE INTO lg_writes(thread_id,checkpoint_ns,checkpoint_id,task_id,"
                "idx,channel,value) VALUES(?,?,?,?,?,?,?)",
                (thread_id, ns, ckpt_id, task_id, idx, channel, pickle.dumps(value)))
        conn.commit()

    # ---- 读 ----
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        conf = config.get("configurable", {})
        thread_id = conf.get("thread_id")
        if not thread_id:
            return None
        ns = conf.get("checkpoint_ns", "")
        conn = _conn()
        row = None
        if conf.get("checkpoint_id"):
            row = conn.execute(
                "SELECT * FROM lg_checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, ns, conf["checkpoint_id"])).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM lg_checkpoints WHERE thread_id=? AND checkpoint_ns=?"
                " ORDER BY rowid DESC LIMIT 1", (thread_id, ns)).fetchone()
        if not row:
            return None
        checkpoint, metadata = pickle.loads(row["checkpoint"])
        pending = []
        for w in conn.execute(
                "SELECT task_id, idx, channel, value FROM lg_writes WHERE thread_id=? AND checkpoint_ns=?"
                " AND checkpoint_id=? ORDER BY task_id, idx",
                (thread_id, ns, row["checkpoint_id"])).fetchall():
            pending.append((w["task_id"], w["channel"], pickle.loads(w["value"])))
        parent_config = None
        if row["parent_checkpoint_id"]:
            parent_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                              "checkpoint_id": row["parent_checkpoint_id"]}}
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                     "checkpoint_id": row["checkpoint_id"]}},
            checkpoint=checkpoint, metadata=metadata,
            parent_config=parent_config, pending_writes=pending)

    def list(self, config: RunnableConfig, *, filter: dict | None = None,
             before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        conf = config.get("configurable", {})
        thread_id = conf.get("thread_id")
        if not thread_id:
            return
        ns = conf.get("checkpoint_ns", "")
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM lg_checkpoints WHERE thread_id=? AND checkpoint_ns=? ORDER BY rowid DESC",
            (thread_id, ns)).fetchall()
        for row in rows:
            checkpoint, metadata = pickle.loads(row["checkpoint"])
            yield CheckpointTuple(
                config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                         "checkpoint_id": row["checkpoint_id"]}},
                checkpoint=checkpoint, metadata=metadata, parent_config=None, pending_writes=[])

    # ---- 异步桥接（langgraph 1.x 的 ainvoke/astream 走异步路径）----
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config: RunnableConfig, *, filter: dict | None = None,
                    before: RunnableConfig | None = None,
                    limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        def _list() -> list[CheckpointTuple]:
            return list(self.list(config, filter=filter, before=before, limit=limit))
        for tup in await asyncio.to_thread(_list):
            yield tup

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint,
                   metadata: CheckpointMetadata, new_versions: dict) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple],
                          task_id: str, task_path: str = "") -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)


checkpointer = SqliteCheckpointSaver()
