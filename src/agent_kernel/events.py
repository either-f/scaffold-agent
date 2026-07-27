"""进程内事件总线。

观测（OTel/Langfuse exporter）、成本统计、审计日志、eval 采集
全部通过订阅事件实现，内核对它们零感知。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .types import Event

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """event_type 支持 "*" 通配订阅全部事件。"""
        self._subs[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._subs.get(event.type, []) + self._subs.get("*", []):
            try:
                handler(event)
            except Exception:  # 订阅者的故障不允许影响主循环
                pass
