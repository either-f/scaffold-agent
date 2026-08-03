"""进程内事件总线。

观测（OTel/Langfuse exporter）、成本统计、审计日志、eval 采集
全部通过订阅事件实现，内核对它们零感知。

SPEC-94 引入 critical vs best-effort 两级投递：
- best-effort（默认，``critical=False``）：与历史行为完全一致，订阅者抛出的
  任何异常都被 ``try/except Exception: pass`` 吞掉，绝不影响主循环。现有
  ``SqliteEventStore`` / ``observability`` / eval 采集全部注册成 best-effort。
- critical（``critical=True``）：订阅者抛出的异常在所有订阅者（含 best-effort）
  跑完之后由 ``publish`` 重新抛出，交给调用方（``AgentKernel._emit``）观测。
  用于"正确性必须可见"的订阅者——例如未来的审计日志、或把事件账本本身升级成
  事实来源时。一个 critical 订阅者失败不会阻止其它 best-effort / critical
  订阅者先跑完；若多个 critical 订阅者同时失败，抛出第一个，其余记入
  ``__cause__`` 链式异常里（``raise ... from``）。

  部署者自行决定把哪个内置 adapter 提升为 critical；本模块默认不把任何
  内置 adapter 注册成 critical——``SqliteEventStore`` / ``observability`` 仍
  保持 best-effort，除非调用方显式 ``subscribe("*", store.handler(), critical=True)``。
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

from .types import Event

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        # SPEC-94: 每个订阅记录 (handler, critical) 二元组；critical 默认 False，
        # 保持现有 subscribe(event_type, handler) 两位置参调用 100% 兼容。
        self._subs: dict[str, list[tuple[Handler, bool]]] = defaultdict(list)
        self._sequences: dict[str, int] = {}
        self._sequence_lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Handler, critical: bool = False) -> None:
        """event_type 支持 "*" 通配订阅全部事件。

        critical=True 的订阅者抛异常时，``publish`` 会在所有订阅者跑完后把异常
        重新抛给调用方；critical=False（默认）的订阅者异常被吞掉，行为不变。
        """
        self._subs[event_type].append((handler, critical))

    def publish(self, event: Event) -> None:
        # fork/context 等非 Kernel 生产者也经 EventBus 统一进入同一 run 的序列。
        run_id = str(event.payload.get("run_id", ""))
        if run_id:
            with self._sequence_lock:
                last = self._sequences.get(run_id, 0)
                if event.sequence <= last:
                    event.sequence = last + 1
                self._sequences[run_id] = event.sequence
        # 收集所有匹配订阅者：精确匹配 + "*" 通配。保持原有顺序（精确在前，通配在后）。
        matches = self._subs.get(event.type, []) + self._subs.get("*", [])
        first_critical_exc: BaseException | None = None
        for handler, critical in matches:
            if critical:
                # critical 订阅者：直接调用，异常先缓存，跑完所有订阅者后再抛。
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001 - 订阅者故障要可见但不打断其它订阅者
                    if first_critical_exc is None:
                        first_critical_exc = exc
            else:
                # best-effort 订阅者：异常一律吞掉，与历史行为一致。
                try:
                    handler(event)
                except Exception:  # 订阅者的故障不允许影响主循环
                    pass
        if first_critical_exc is not None:
            raise first_critical_exc
