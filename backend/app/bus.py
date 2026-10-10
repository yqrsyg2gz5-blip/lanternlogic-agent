"""事件总线 —— 每个任务的订阅者队列，SSE 实时推送用。

发布/订阅都在事件循环内完成，不落盘（落盘是 store 的职责）。
慢消费者队列满时丢事件：SSE 断线后客户端会用 after_seq/Last-Event-ID 补拉（契约二 §4）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from .schemas import EventEnvelope

_QUEUE_SIZE = 1000


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[EventEnvelope]]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue[EventEnvelope]:
        q: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subs[task_id].add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue[EventEnvelope]) -> None:
        self._subs[task_id].discard(q)

    def publish(self, ev: EventEnvelope) -> None:
        for q in list(self._subs.get(ev.task_id, ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # 丢事件优于阻塞循环；客户端可用 after_seq 补
