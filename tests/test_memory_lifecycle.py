"""TTL + Importance 生命周期测试：SqliteMemory / GraphMemory 的 add(ttl_seconds=...)
过期后从 search 消失、prune_expired 能物理删除；importance 影响排序优先级。

运行：PYTHONPATH=src python3 tests/test_memory_lifecycle.py   （也兼容 pytest）
"""
import sys
import time

sys.path.insert(0, "src")

from agent_kernel.adapters.memory.graph import GraphMemory
from agent_kernel.adapters.memory.sqlite import SqliteMemory


def test_sqlite_memory_ttl_expires_from_search():
    mem = SqliteMemory()
    mem.add("run-1", "user", "临时事实 alpha", ttl_seconds=-1)  # 已过期
    mem.add("run-1", "user", "长期事实 alpha", ttl_seconds=None)
    hits = mem.search("alpha", k=10)
    assert hits == ["长期事实 alpha"]


def test_sqlite_memory_prune_expired_deletes_rows():
    mem = SqliteMemory()
    mem.add("run-1", "user", "过期事实 beta", ttl_seconds=-1)
    assert mem.prune_expired() == 1
    assert mem.prune_expired() == 0


def test_sqlite_memory_importance_orders_before_recency():
    mem = SqliteMemory()
    mem.add("run-1", "user", "普通事实 gamma", importance=1.0)
    time.sleep(0.01)
    mem.add("run-1", "user", "重要事实 gamma", importance=5.0)
    hits = mem.search("gamma", k=10)
    assert hits[0] == "重要事实 gamma"


def test_graph_memory_ttl_and_prune():
    graph = GraphMemory()
    graph.add("run-1", "user", "临时图事实 delta", ttl_seconds=-1)
    graph.add("run-1", "user", "长期图事实 delta", ttl_seconds=None)
    assert graph.search("delta", k=10) == ["长期图事实 delta"]
    assert graph.prune_expired() == 1


if __name__ == "__main__":
    test_sqlite_memory_ttl_expires_from_search()
    test_sqlite_memory_prune_expired_deletes_rows()
    test_sqlite_memory_importance_orders_before_recency()
    test_graph_memory_ttl_and_prune()
    print("ok")
