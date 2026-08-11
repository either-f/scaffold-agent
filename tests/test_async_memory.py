"""AsyncMemory 单测：add() 立即返回不阻塞、flush() 后写入全部落地、search() 同步
直通、写入异常触发 on_error 回调而不是抛给调用方。

运行：PYTHONPATH=src python3 tests/test_async_memory.py   （也兼容 pytest）
"""
import sys
import threading
import time

sys.path.insert(0, "src")

from agent_kernel.adapters.memory.async_write import AsyncMemory
from agent_kernel.ports import MemoryPort


class SlowDictMemory(MemoryPort):
    def __init__(self, delay: float = 0.0) -> None:
        self.items: list[tuple[str, str, str]] = []
        self.delay = delay
        self._lock = threading.Lock()

    def add(self, run_id: str, role: str, content: str) -> None:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.items.append((run_id, role, content))

    def search(self, query: str, k: int = 5) -> list[str]:
        return [c for _, _, c in self.items[:k]]


class FailingMemory(MemoryPort):
    def add(self, run_id: str, role: str, content: str) -> None:
        raise RuntimeError("write failed")

    def search(self, query: str, k: int = 5) -> list[str]:
        return []


def test_add_returns_immediately_without_waiting_for_slow_write():
    inner = SlowDictMemory(delay=0.1)
    mem = AsyncMemory(inner)
    start = time.time()
    mem.add("r1", "user", "hello")
    elapsed = time.time() - start
    assert elapsed < 0.05  # 远小于 inner 的 0.1s 延迟，证明没等
    mem.flush()


def test_flush_waits_for_all_pending_writes_to_land():
    inner = SlowDictMemory(delay=0.03)
    mem = AsyncMemory(inner, max_workers=4)
    for i in range(5):
        mem.add("r1", "user", f"msg{i}")
    mem.flush()
    assert len(inner.items) == 5


def test_search_forwards_synchronously_to_inner():
    inner = SlowDictMemory()
    inner.add("r1", "user", "hello world")
    mem = AsyncMemory(inner)
    assert mem.search("hello", k=5) == ["hello world"]


def test_write_failure_reported_via_on_error_not_raised():
    errors = []
    lock = threading.Lock()

    def on_error(run_id, role, content, exc):
        with lock:
            errors.append((run_id, role, content, str(exc)))

    mem = AsyncMemory(FailingMemory(), on_error=on_error)
    mem.add("r1", "user", "boom")  # 不该在这里抛异常
    mem.flush()
    with lock:
        assert errors == [("r1", "user", "boom", "write failed")]


def test_no_on_error_configured_silently_swallows_failure():
    mem = AsyncMemory(FailingMemory())
    mem.add("r1", "user", "boom")  # 不该抛异常
    mem.flush()  # 不该抛异常


def test_flush_allows_reuse_afterwards():
    inner = SlowDictMemory()
    mem = AsyncMemory(inner)
    mem.add("r1", "user", "first")
    mem.flush()
    mem.add("r1", "user", "second")
    mem.flush()
    assert len(inner.items) == 2


if __name__ == "__main__":
    test_add_returns_immediately_without_waiting_for_slow_write()
    test_flush_waits_for_all_pending_writes_to_land()
    test_search_forwards_synchronously_to_inner()
    test_write_failure_reported_via_on_error_not_raised()
    test_no_on_error_configured_silently_swallows_failure()
    test_flush_allows_reuse_afterwards()
    print("OK: AsyncMemory 测试全部通过")
