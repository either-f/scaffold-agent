"""Hybrid GraphRAG 单测：RRF 融合排序性质、多跳 BFS 边界、BM25 真词频排序（对比
SqliteMemory 的 LIKE 检索）、Markdown 切片重叠。

运行：PYTHONPATH=src python3 tests/test_hybrid_rag.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "evals")

from agent_kernel.adapters.memory.bm25 import Bm25Memory
from agent_kernel.adapters.memory.hybrid_rag import multi_hop_search, reciprocal_rank_fusion
from agent_kernel.adapters.memory.sqlite import SqliteMemory
from run_hybrid_rag import chunk_markdown


def test_rrf_ranks_multi_list_hits_above_single_list_hits():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
    order = [item for item, _ in fused]
    assert order.index("a") < order.index("c")  # a 两路都命中，排在只出现一路的 c 前面
    assert order.index("b") < order.index("d")
    assert order[0] == "a"  # a 在两个列表里都排第一，融合分数应最高


def test_rrf_empty_lists_return_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def _star_graph():
    """n1 -R-> n2 -R-> n3 -R-> n4，纯字典模拟 get_neighbors。"""
    edges = {
        "n1": [("n1", "R", "n2", "outgoing")],
        "n2": [("n1", "R", "n2", "incoming"), ("n2", "R", "n3", "outgoing")],
        "n3": [("n2", "R", "n3", "incoming"), ("n3", "R", "n4", "outgoing")],
        "n4": [("n3", "R", "n4", "incoming")],
    }
    return lambda node: edges.get(node, [])


def test_multi_hop_respects_hop_limit():
    neighbors_fn = _star_graph()
    one_hop = multi_hop_search(neighbors_fn, ["n1"], hops=1, k=20)
    three_hop = multi_hop_search(neighbors_fn, ["n1"], hops=3, k=20)
    assert any("n2" in e for e in one_hop)
    assert not any("n4" in e for e in one_hop)  # 3 跳外的边，1 跳找不到
    assert any("n4" in e for e in three_hop)  # 3 跳能找到


def test_multi_hop_cyclic_graph_does_not_hang():
    def neighbors_fn(node):
        return {
            "a": [("a", "R", "b", "outgoing")],
            "b": [("a", "R", "b", "incoming"), ("b", "R", "a", "outgoing")],
        }.get(node, [])

    result = multi_hop_search(neighbors_fn, ["a"], hops=5, k=20)  # 挂死的话测试会超时失败
    assert len(result) <= 2  # 环里只有 2 条边，visited 生效不会无限累积


def test_bm25_ranks_higher_term_frequency_above_sqlite_like():
    bm25 = Bm25Memory()
    bm25.add("r1", "user", "apple apple apple banana")
    bm25.add("r1", "user", "apple banana banana banana")
    ranked = bm25.search("apple", k=2)
    assert ranked[0] == "apple apple apple banana"  # 词频更高的排前面，真做了 BM25 统计

    like_engine = SqliteMemory()
    like_engine.add("r1", "user", "apple apple apple banana")
    like_engine.add("r1", "user", "apple banana banana banana")
    like_hits = like_engine.search("apple", k=2)
    assert set(like_hits) == set(ranked)  # 两边都命中同一批文档
    assert len(like_hits) == 2  # 但 LIKE 检索不做词频排序，只按时间倒序（不对比顺序）


def test_bm25_no_overlap_returns_empty():
    bm25 = Bm25Memory()
    bm25.add("r1", "user", "completely unrelated content here")
    assert bm25.search("nonexistent query terms", k=5) == []


def test_bm25_parent_child_returns_full_parent_not_fragment():
    bm25 = Bm25Memory()
    parent = (
        "Section about rocket engines: the Aurora rocket uses a methalox engine, "
        "cost overruns delayed launch by 6 months, team lead is Li Lei."
    )
    children = [
        "the Aurora rocket uses a methalox engine",
        "cost overruns delayed launch by 6 months",
        "team lead is Li Lei",
    ]
    bm25.add_parent_child("r1", "assistant", parent, children)

    hits = bm25.search("methalox engine", k=5)
    assert hits == [parent]  # 命中 child 后返回完整 parent，不是命中的那句碎片


def test_bm25_parent_child_dedups_when_multiple_children_hit():
    bm25 = Bm25Memory()
    parent = "rocket engine team lead cost overrun launch delay"
    bm25.add_parent_child(
        "r1", "assistant", parent, ["rocket engine cost overrun", "team lead launch delay"]
    )
    hits = bm25.search("rocket engine team lead cost launch", k=5)
    assert hits.count(parent) == 1  # 两个 child 都命中，parent 只返回一次


def test_bm25_parent_child_does_not_affect_standalone_docs():
    bm25 = Bm25Memory()
    bm25.add_parent_child("r1", "assistant", "parent about rockets", ["rocket child fragment"])
    bm25.add("r1", "assistant", "unrelated standalone document about weather patterns")
    assert bm25.search("weather patterns", k=5) == [
        "unrelated standalone document about weather patterns"
    ]


def test_chunk_markdown_splits_long_text_with_overlap():
    long_text = "# Heading\n" + ("word " * 300)
    chunks = chunk_markdown(long_text, max_chars=200, overlap=50)
    assert len(chunks) > 1
    assert chunks[0][-30:] in chunks[1]  # 相邻块首尾有重叠内容


def test_chunk_markdown_short_text_single_chunk():
    assert chunk_markdown("just a short chunk") == ["just a short chunk"]


def test_chunk_markdown_empty_input():
    assert chunk_markdown("   ") == []


if __name__ == "__main__":
    test_rrf_ranks_multi_list_hits_above_single_list_hits()
    test_rrf_empty_lists_return_empty()
    test_multi_hop_respects_hop_limit()
    test_multi_hop_cyclic_graph_does_not_hang()
    test_bm25_ranks_higher_term_frequency_above_sqlite_like()
    test_bm25_no_overlap_returns_empty()
    test_bm25_parent_child_returns_full_parent_not_fragment()
    test_bm25_parent_child_dedups_when_multiple_children_hit()
    test_bm25_parent_child_does_not_affect_standalone_docs()
    test_chunk_markdown_splits_long_text_with_overlap()
    test_chunk_markdown_short_text_single_chunk()
    test_chunk_markdown_empty_input()
    print("OK: Hybrid GraphRAG 测试全部通过")
