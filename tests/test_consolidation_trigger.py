"""ConsolidationTriggerMemory 单测：soft_threshold 触发一次、hard_cap 强制触发并清零、
不同 run_id 互相隔离、search 直通。

运行：PYTHONPATH=src python3 tests/test_consolidation_trigger.py   （也兼容 pytest）
"""
import sys
import threading
import time

sys.path.insert(0, "src")

from agent_kernel.adapters.memory.consolidation_trigger import ConsolidationTriggerMemory
from agent_kernel.ports import MemoryPort


class DictMemory(MemoryPort):
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, run_id: str, role: str, content: str) -> None:
        self.items.append((run_id, role, content))

    def search(self, query: str, k: int = 5) -> list[str]:
        return [c for _, _, c in self.items[:k]]


def test_soft_threshold_triggers_exactly_once():
    episodic = DictMemory()
    fired: list[str] = []
    mem = ConsolidationTriggerMemory(episodic, fired.append, soft_threshold=3, hard_cap=100)
    for i in range(5):
        mem.add("r1", "user", f"msg{i}")
    assert fired == ["r1"]  # 第 3 条触发一次，第 4/5 条不重复触发


def test_hard_cap_forces_trigger_and_resets_count():
    episodic = DictMemory()
    fired: list[str] = []
    mem = ConsolidationTriggerMemory(episodic, fired.append, soft_threshold=3, hard_cap=5)
    for i in range(5):
        mem.add("r1", "user", f"msg{i}")
    assert fired == ["r1", "r1"]  # 第 3 条 soft 触发一次，第 5 条 hard 再触发一次

    for i in range(3):
        mem.add("r1", "user", f"more{i}")
    assert fired == ["r1", "r1", "r1"]  # 清零后重新累计到 soft_threshold(3) 再触发一次


def test_run_ids_counted_independently():
    episodic = DictMemory()
    fired: list[str] = []
    mem = ConsolidationTriggerMemory(episodic, fired.append, soft_threshold=2, hard_cap=100)
    mem.add("r1", "user", "a")
    mem.add("r2", "user", "b")
    mem.add("r1", "user", "c")
    assert fired == ["r1"]
    mem.add("r2", "user", "d")
    assert fired == ["r1", "r2"]


def test_invalid_thresholds_reject_at_construction():
    episodic = DictMemory()
    try:
        ConsolidationTriggerMemory(episodic, lambda r: None, soft_threshold=10, hard_cap=5)
        assert False, "hard_cap < soft_threshold 应该拒绝"
    except ValueError:
        pass


def test_search_forwards_to_episodic():
    episodic = DictMemory()
    episodic.add("r1", "user", "hello world")
    mem = ConsolidationTriggerMemory(episodic, lambda r: None, soft_threshold=100, hard_cap=200)
    assert mem.search("hello", k=5) == ["hello world"]


def test_start_timer_without_configured_seconds_raises():
    episodic = DictMemory()
    mem = ConsolidationTriggerMemory(episodic, lambda r: None, soft_threshold=100, hard_cap=200)
    try:
        mem.start_timer()
        assert False, "未配置 timer_seconds 应该拒绝"
    except ValueError:
        pass


def test_timer_fires_below_soft_threshold_and_resets_count():
    episodic = DictMemory()
    fired = []
    lock = threading.Lock()

    def on_trigger(run_id):
        with lock:
            fired.append(run_id)

    # soft_threshold 故意设很高，证明是墙钟触发的而不是计数触发
    mem = ConsolidationTriggerMemory(
        episodic, on_trigger, soft_threshold=1000, hard_cap=2000, timer_seconds=0.03
    )
    mem.add("r1", "user", "just one message")
    try:
        mem.start_timer()
        time.sleep(0.1)
    finally:
        mem.stop_timer()

    with lock:
        assert fired.count("r1") >= 1
    assert mem._counts.get("r1", 0) == 0  # 触发后计数清零


def test_timer_does_not_fire_for_run_ids_with_zero_pending_count():
    episodic = DictMemory()
    fired = []
    mem = ConsolidationTriggerMemory(
        episodic, fired.append, soft_threshold=1000, hard_cap=2000, timer_seconds=0.03
    )
    try:
        mem.start_timer()
        time.sleep(0.08)
    finally:
        mem.stop_timer()
    assert fired == []  # 没写过消息，没什么可巩固的


def test_stop_timer_is_idempotent_and_stops_further_firing():
    episodic = DictMemory()
    fired = []
    mem = ConsolidationTriggerMemory(
        episodic, fired.append, soft_threshold=1000, hard_cap=2000, timer_seconds=0.02
    )
    mem.add("r1", "user", "msg")
    mem.start_timer()
    time.sleep(0.05)
    mem.stop_timer()
    mem.stop_timer()  # 第二次 stop 不该报错
    count_after_stop = len(fired)
    time.sleep(0.05)
    assert len(fired) == count_after_stop  # 停掉之后不再触发


if __name__ == "__main__":
    test_soft_threshold_triggers_exactly_once()
    test_hard_cap_forces_trigger_and_resets_count()
    test_run_ids_counted_independently()
    test_invalid_thresholds_reject_at_construction()
    test_search_forwards_to_episodic()
    test_start_timer_without_configured_seconds_raises()
    test_timer_fires_below_soft_threshold_and_resets_count()
    test_timer_does_not_fire_for_run_ids_with_zero_pending_count()
    test_stop_timer_is_idempotent_and_stops_further_firing()
    print("OK: ConsolidationTriggerMemory 测试全部通过")
