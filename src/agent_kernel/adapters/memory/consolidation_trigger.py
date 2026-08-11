"""ConsolidationTriggerMemory：episodic 写入达到阈值时同步触发巩固回调，
外加可选的墙钟定时触发——对齐 FoxChat"定时器 45s / 节点内计数 30 / 硬上限 40"
三级触发的全部三级（此前版本只做了计数两级）。

soft_threshold 命中后不重复触发（等 reset），hard_cap 命中后强制触发并清零
计数。timer_seconds 配置后可调 start_timer()：后台 daemon 线程每隔
timer_seconds 秒，对"当前还有未落盘计数"的 run_id 各触发一次并清零——覆盖
"消息量不大但会话已经开很久，一直没到 soft_threshold"的场景。

on_trigger 只是通知，不在这里调 evals/run_consolidation.consolidate_run——那需要
model/long_term/preferences 三个依赖，调用方按自己的巩固管线组装。

ponytail: 不在 __init__ 里自动起线程——"构造零副作用"，测试/离线场景不需要真的
起后台线程；调用方需要墙钟触发时显式调 start_timer()，进程退出前调
stop_timer()（daemon=True 兜底，但显式 stop 更干净、测试也需要它避免残留线程）。
"""
from __future__ import annotations

import threading
from typing import Callable

from ...ports import MemoryPort

OnTrigger = Callable[[str], None]


class ConsolidationTriggerMemory(MemoryPort):
    def __init__(
        self,
        episodic: MemoryPort,
        on_trigger: OnTrigger,
        soft_threshold: int = 18,
        hard_cap: int = 40,
        timer_seconds: float | None = None,
    ) -> None:
        if hard_cap < soft_threshold:
            raise ValueError("hard_cap 不能小于 soft_threshold")
        self.episodic = episodic
        self.on_trigger = on_trigger
        self.soft_threshold = soft_threshold
        self.hard_cap = hard_cap
        self.timer_seconds = timer_seconds
        self._counts: dict[str, int] = {}
        self._soft_triggered: set[str] = set()
        self._lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def add(self, run_id: str, role: str, content: str) -> None:
        self.episodic.add(run_id, role, content)
        with self._lock:
            count = self._counts.get(run_id, 0) + 1
            self._counts[run_id] = count
            hard = count >= self.hard_cap
            soft = count >= self.soft_threshold and run_id not in self._soft_triggered
            if hard:
                self._counts[run_id] = 0
                self._soft_triggered.discard(run_id)
            elif soft:
                self._soft_triggered.add(run_id)

        if hard or soft:
            self.on_trigger(run_id)

    def search(self, query: str, k: int = 5) -> list[str]:
        return self.episodic.search(query, k=k)

    # --------------------------------------------------------------- 定时触发
    def start_timer(self) -> None:
        if self.timer_seconds is None:
            raise ValueError("未配置 timer_seconds")
        if self._timer_thread is not None:
            return
        self._stop_event.clear()
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def stop_timer(self) -> None:
        self._stop_event.set()
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=max(self.timer_seconds or 0.0, 1.0))
            self._timer_thread = None

    def _timer_loop(self) -> None:
        assert self.timer_seconds is not None
        while not self._stop_event.wait(self.timer_seconds):
            with self._lock:
                due = [run_id for run_id, count in self._counts.items() if count > 0]
                for run_id in due:
                    self._counts[run_id] = 0
                    self._soft_triggered.discard(run_id)
            for run_id in due:
                self.on_trigger(run_id)
