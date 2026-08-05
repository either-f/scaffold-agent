"""Hybrid GraphRAG 融合层：向量 + BM25 关键词 + 图谱三路召回，RRF（Reciprocal Rank
Fusion）融合排序，图谱一路带多跳扩展。三路召回器都只是标准 MemoryPort，
`HybridGraphRAG` 本身也实现 MemoryPort——跟 CompositeMemory 一样，内核对新概念
零感知，`kernel.py`/`ports.py` 零改动。

ingestion（Markdown 清洗/切片/实体关系抽取写图谱）不在这里，是离线批处理脚本
evals/run_hybrid_rag.py 的职责——跟 evals/run_consolidation.py 的"提炼交给离线脚本"
是同一个理由：LLM 抽取成本高，不能挂在同步 add() 路径上。
"""
from __future__ import annotations

from typing import Callable

from ...ports import MemoryPort

NeighborsFn = Callable[[str], list[tuple[str, str, str, str]]]
EdgesFn = Callable[[], list[tuple[str, str, str]]]


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], k: int = 60
) -> list[tuple[str, float]]:
    """标准 RRF：score(d) = Σ 1/(k + rank_in_list)，k=60 是原论文
    （Cormack et al., 2009）的标准常数。按融合分数降序返回 [(content, score), ...]。"""
    scores: dict[str, float] = {}
    order: list[str] = []  # 保留首次出现顺序，分数相同时 tie-break 稳定
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            if item not in scores:
                order.append(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(((item, scores[item]) for item in order), key=lambda kv: kv[1], reverse=True)


def multi_hop_search(
    neighbors_fn: NeighborsFn,
    seed_nodes: list[str],
    hops: int = 2,
    k: int = 20,
) -> list[str]:
    """从种子节点出发 BFS 扩展 `hops` 跳，每条边三元组文本化（"subject relation object"）
    塞进结果列表喂给 RRF 当"内容"。`neighbors_fn` 由调用方注入的闭包——
    GraphMemory.get_neighbors 要 namespace 参数、Neo4jGraphMemory.get_neighbors 不要
    （两个 adapter 签名本来就不一样，见 ADR-0006），闭包屏蔽这个差异，不改任何一个
    现有文件。`visited` 去重防止环。"""
    visited: set[str] = set(seed_nodes)
    frontier = list(seed_nodes)
    collected: list[str] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for _ in range(hops):
        next_frontier: list[str] = []
        for node in frontier:
            for subject, relation, obj, direction in neighbors_fn(node):
                edge = (subject, relation, obj)
                if edge not in seen_edges:
                    seen_edges.add(edge)
                    collected.append(f"{subject} {relation} {obj}")
                neighbor = obj if direction == "outgoing" else subject
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
        if len(collected) >= k or not frontier:
            break
    return collected[:k]


class HybridGraphRAG(MemoryPort):
    def __init__(
        self,
        vector: MemoryPort,
        keyword: MemoryPort,
        graph,
        neighbors_fn: NeighborsFn,
        edges_fn: EdgesFn,
        rrf_k: int = 60,
        hops: int = 2,
        per_leg_k: int = 10,
    ) -> None:
        self.vector = vector
        self.keyword = keyword
        self.graph = graph
        self.neighbors_fn = neighbors_fn
        self.edges_fn = edges_fn
        self.rrf_k = rrf_k
        self.hops = hops
        self.per_leg_k = per_leg_k

    def add(self, run_id: str, role: str, content: str) -> None:
        # 只转发给 vector + keyword 两路原始内容索引；graph 的实体/关系写入是离线
        # ingestion 的职责（见模块 docstring），跟 CompositeMemory.add() 只落 episodic
        # 不重复写 semantic 是同一个理由。
        self.vector.add(run_id, role, content)
        self.keyword.add(run_id, role, content)

    def search(self, query: str, k: int = 5) -> list[str]:
        vector_hits = self.vector.search(query, k=self.per_leg_k)
        keyword_hits = self.keyword.search(query, k=self.per_leg_k)
        graph_hits = self._graph_search(query, self.per_leg_k)
        fused = reciprocal_rank_fusion([vector_hits, keyword_hits, graph_hits], k=self.rrf_k)
        return [content for content, _ in fused[:k]]

    def _graph_search(self, query: str, k: int) -> list[str]:
        fact_hits = self.graph.search(query, k=k)
        seeds = self._seed_entities(query)
        hop_hits = multi_hop_search(self.neighbors_fn, seeds, hops=self.hops, k=k) if seeds else []
        merged = list(dict.fromkeys([*fact_hits, *hop_hits]))
        return merged[:k]

    def _seed_entities(self, query: str) -> list[str]:
        # ponytail: 简单子串包含做实体识别（已知实体名是否出现在 query 文本里），真
        # 实体链接（entity linking）有歧义问题，先够用。
        edges = self.edges_fn()
        known = {s for s, _, _ in edges} | {o for _, _, o in edges}
        return [entity for entity in known if entity and entity in query]
