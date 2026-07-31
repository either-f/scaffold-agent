"""CompositeMemory：把短期情景记忆（全量最近消息）、长期语义记忆（提炼后的事实）、
可选图谱记忆统一封装成一个 MemoryPort，对内核和 Planner 零感知——都只看到 add/search。

写：只转发给 episodic，不重复写 semantic。长期语义记忆只应该存"提炼后的事实"，不是
每条原始消息（跟 ADR-0004 的既定边界一致）；运行时写入原始消息，离线巩固脚本
（evals/run_consolidation.py）负责从 episodic 提炼后再写进 semantic。

查：semantic（已提炼的知识，优先级最高）+ graph（若配置，关系型证据）+ episodic
（原始细节，兜底）三路合并去重，语义/关系命中优先于原始消息，保证"既保留细节又有
长期沉淀"里"长期沉淀"排在前面。
"""
from __future__ import annotations

from ..ports import MemoryPort


class CompositeMemory(MemoryPort):
    def __init__(
        self,
        episodic: MemoryPort,
        semantic: MemoryPort,
        graph: MemoryPort | None = None,
        episodic_k: int = 5,
        semantic_k: int = 5,
        graph_k: int = 3,
    ) -> None:
        self.episodic = episodic
        self.semantic = semantic
        self.graph = graph
        self.episodic_k = episodic_k
        self.semantic_k = semantic_k
        self.graph_k = graph_k

    def add(self, run_id: str, role: str, content: str) -> None:
        self.episodic.add(run_id, role, content)

    def search(self, query: str, k: int = 5) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for source, source_k in (
            (self.semantic, self.semantic_k),
            (self.graph, self.graph_k),
            (self.episodic, self.episodic_k),
        ):
            if source is None:
                continue
            for hit in source.search(query, k=source_k):
                if hit not in seen:
                    seen.add(hit)
                    merged.append(hit)
        return merged[:k]
