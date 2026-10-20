# -*- coding: utf-8 -*-
"""
事件总线（SSE 进度推送）
========================
LangGraph 节点（含子图内部节点）通过 EventBus 广播执行进度，
FastAPI 的 SSE 端点按 thread_id 订阅并推送给前端。
与 langgraph 的 stream 机制解耦：子图/中断场景下依然可靠。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class _Channel:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.cond = threading.Condition()

    def publish(self, event: dict) -> None:
        with self.cond:
            event = dict(event)
            event.setdefault("ts", time.time())
            self.events.append(event)
            self.cond.notify_all()

    def snapshot(self) -> list[dict]:
        with self.cond:
            return list(self.events)

    def wait(self, timeout: float = 0.5) -> None:
        with self.cond:
            self.cond.wait(timeout)


class EventBus:
    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = defaultdict(_Channel)
        self._lock = threading.Lock()

    def channel(self, thread_id: str) -> _Channel:
        with self._lock:
            return self._channels[thread_id]

    def emit(self, thread_id: str, event: dict) -> None:
        self.channel(thread_id).publish(event)

    def snapshot(self, thread_id: str) -> list[dict]:
        return self.channel(thread_id).snapshot()

    def cleanup(self, thread_id: str) -> None:
        with self._lock:
            self._channels.pop(thread_id, None)


event_bus = EventBus()


def emit_node_progress(thread_id: str, agent: str, node: str, message: str,
                       extra: dict | None = None) -> None:
    """节点进度事件（每个 LangGraph 节点入口调用）。"""
    event = {"type": "progress", "agent": agent, "node": node, "message": message}
    if extra:
        event.update(extra)
    event_bus.emit(thread_id, event)
