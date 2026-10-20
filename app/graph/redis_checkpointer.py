# -*- coding: utf-8 -*-
"""
Redis Checkpointer（Checkpointer 的 Redis 实现）
================================================
与 `checkpointer.py` 的 SqliteCheckpointSaver 实现同一套 BaseCheckpointSaver 接口，
存储后端换成 Redis（Docker 部署，默认 8379 端口），对应文档 3.6 生产化路径之一。

为什么值得换：
  1. 并发友好：redis-py 内置连接池，天然多线程/多实例共享——SQLite 单连接+全局锁
     在此是瓶颈（详见 docs 中并发讨论）
  2. TTL 原生支持：checkpoint key 可直接 set 过期（文档：State 保留 90 天），
     不用自己写清理 cron
  3. 不停机重启：SQLite 单文件在 EXCLUSIVE 锁下降级时同时只允许一个进程；
     Redis 多实例共享同一份断点状态，HitL 恢复不丢

数据结构（key 设计）：
  lg:cp:{thread_id}:{ns}:latest              string  最新 checkpoint_id
  lg:cp:{thread_id}:{ns}:seq                 string  自增序号（zset score 用）
  lg:cp:{thread_id}:{ns}:ids                 zset    checkpoint_id 按写入顺序
  lg:cp:{thread_id}:{ns}:{checkpoint_id}     string  pickle(checkpoint, metadata)
  lg:cp:{thread_id}:{ns}:{checkpoint_id}:w   hash    pending_writes（field=task_id:idx）

序列化沿用 pickle（与 SQLite 版一致；生产可换 JsonPlusSerializer，见 README 讨论）。
TTL 默认 0（不过期），设 FUNDAGENT_CHECKPOINTER_TTL_DAYS=90 即 90 天自动清理。
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from collections.abc import AsyncIterator, Iterator, Sequence

import redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ..config import settings

logger = logging.getLogger("fundagent.checkpointer")

_PREFIX = "lg:cp"


def _keys(thread_id: str, ns: str) -> dict:
    base = f"{_PREFIX}:{thread_id}:{ns}"
    return {
        "latest": f"{base}:latest",
        "seq": f"{base}:seq",
        "ids": f"{base}:ids",
        "ckpt": lambda ckpt_id: f"{base}:{ckpt_id}",
        "writes": lambda ckpt_id: f"{base}:{ckpt_id}:w",
    }


class RedisCheckpointSaver(BaseCheckpointSaver):
    """基于 Redis 的 LangGraph Checkpointer（接口对齐 SQLite/Oracle 版）。"""

    serde: SerializerProtocol = JsonPlusSerializer()

    def __init__(self) -> None:
        self._client = redis.Redis.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            decode_responses=False,      # 存 pickle bytes
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=30,
        )
        self._ttl = settings.checkpoint_ttl_days * 86400 if settings.checkpoint_ttl_days > 0 else None
        self._check_connection()

    def _check_connection(self) -> None:
        """启动即探测：连不上直接失败（断点丢失会导致 HitL 恢复找不到状态，
        静默降级到 SQLite 更危险，所以这里明确报错）。"""
        try:
            self._client.ping()
        except redis.exceptions.AuthenticationError as e:
            raise RuntimeError(
                f"Redis 认证失败（{settings.redis_url}）：请设置环境变量 "
                f"FUNDAGENT_REDIS_PASSWORD。原始错误：{e}") from e
        except Exception as e:
            raise RuntimeError(
                f"Redis 连接失败（{settings.redis_url}）：请确认 Docker 容器已启动、"
                f"端口映射正确。启动示例：docker run -d --name fundagent-redis "
                f"-p 8379:6379 redis:7-alpine。原始错误：{e}") from e

    # ------------------------------------------------------------------
    # 同步接口（langgraph 的 invoke 路径）
    # ------------------------------------------------------------------
    def put(self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata, new_versions: dict) -> RunnableConfig:
        conf = config.get("configurable", {})
        thread_id, ns = conf["thread_id"], conf.get("checkpoint_ns", "")
        k = _keys(thread_id, ns)
        blob = pickle.dumps((checkpoint, metadata))
        pipe = self._client.pipeline(transaction=False)
        pipe.set(k["ckpt"](checkpoint["id"]), blob)
        seq = self._client.incr(k["seq"])
        pipe.zadd(k["ids"], {checkpoint["id"]: seq})
        pipe.set(k["latest"], checkpoint["id"])
        if self._ttl:
            pipe.expire(k["ckpt"](checkpoint["id"]), self._ttl)
            pipe.expire(k["ids"], self._ttl)
            pipe.expire(k["latest"], self._ttl)
        pipe.execute()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                 "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple],
                   task_id: str, task_path: str = "") -> None:
        conf = config.get("configurable", {})
        thread_id, ns = conf["thread_id"], conf.get("checkpoint_ns", "")
        k = _keys(thread_id, ns)
        key = k["writes"](conf["checkpoint_id"])
        pipe = self._client.pipeline(transaction=False)
        for idx, (channel, value) in enumerate(writes):
            pipe.hset(key, f"{task_id}:{idx}", pickle.dumps((channel, value)))
        if self._ttl:
            pipe.expire(key, self._ttl)
        pipe.execute()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        conf = config.get("configurable", {})
        thread_id = conf.get("thread_id")
        if not thread_id:
            return None
        ns = conf.get("checkpoint_ns", "")
        k = _keys(thread_id, ns)
        ckpt_id = conf.get("checkpoint_id")
        if not ckpt_id:
            raw = self._client.get(k["latest"])
            ckpt_id = raw.decode() if raw else None
        if not ckpt_id:
            return None
        blob = self._client.get(k["ckpt"](ckpt_id))
        if not blob:
            return None
        checkpoint, metadata = pickle.loads(blob)
        pending = []
        writes_raw = self._client.hgetall(k["writes"](ckpt_id))
        for field, value in sorted(writes_raw.items()):
            task_id, idx = field.decode().rsplit(":", 1)
            channel, val = pickle.loads(value)
            pending.append((task_id, channel, val))
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                     "checkpoint_id": ckpt_id}},
            checkpoint=checkpoint, metadata=metadata,
            parent_config=None, pending_writes=pending)

    def list(self, config: RunnableConfig, *, filter: dict | None = None,
             before: RunnableConfig | None = None,
             limit: int | None = None) -> Iterator[CheckpointTuple]:
        conf = config.get("configurable", {})
        thread_id = conf.get("thread_id")
        if not thread_id:
            return
        ns = conf.get("checkpoint_ns", "")
        k = _keys(thread_id, ns)
        ids = self._client.zrevrange(k["ids"], 0, (limit or 50) - 1)
        for raw_id in ids:
            blob = self._client.get(k["ckpt"](raw_id.decode()))
            if not blob:
                continue
            checkpoint, metadata = pickle.loads(blob)
            yield CheckpointTuple(
                config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                                         "checkpoint_id": raw_id.decode()}},
                checkpoint=checkpoint, metadata=metadata,
                parent_config=None, pending_writes=[])

    # ------------------------------------------------------------------
    # 异步接口（langgraph 的 ainvoke/astream 路径，asyncio.to_thread 桥接）
    # ------------------------------------------------------------------
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


def make_redis_checkpointer() -> RedisCheckpointSaver:
    """工厂函数：连接失败时抛出带修复指引的 RuntimeError。"""
    return RedisCheckpointSaver()
