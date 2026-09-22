"""CompositeMemory：把短期情景记忆（全量最近消息）、长期语义记忆（提炼后的事实）、
可选图谱记忆统一封装成一个 MemoryPort，对内核和 Planner 零感知——都只看到 add/search。

写：只转发给 episodic，不重复写 semantic。长期语义记忆只应该存"提炼后的事实"，不是
每条原始消息（跟 ADR-0004 的既定边界一致）；运行时写入原始消息，离线巩固脚本
（evals/run_consolidation.py）负责从 episodic 提炼后再写进 semantic。

查：semantic（已提炼的知识，优先级最高）+ graph（若配置，关系型证据）+ episodic
（原始细节，兜底）三路合并去重，语义/关系命中优先于原始消息，保证"既保留细节又有
长期沉淀"里"长期沉淀"排在前面。

SPEC-90：合并去重时保留每条命中的 source（不再丢弃 provenance），返回顺序仍为
semantic → graph → episodic。子适配器现在返回 MemoryHit（str 子类），基于 content
去重（str __eq__/__hash__ 按 content 比较），同一 content 只保留首次（最高优先级）
出现的 source。
"""
from __future__ import annotations

from ...ports import MemoryPort
from ...types import MemoryHit


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

    def add(self, run_id: str, role: str, content: str, identity: str | None = None) -> None:
        self.episodic.add(run_id, role, content, identity=identity)

    def search(self, query: str, k: int = 5, identity: str | None = None) -> list[MemoryHit]:
        seen: set[str] = set()
        merged: list[MemoryHit] = []
        for source, source_k, source_label in (
            (self.semantic, self.semantic_k, "semantic"),
            (self.graph, self.graph_k, "graph"),
            (self.episodic, self.episodic_k, "episodic"),
        ):
            if source is None:
                continue
            for hit in source.search(query, k=source_k, identity=identity):
                # MemoryHit 是 str 子类，按 content 去重；保留首次（最高优先级 source）
                if hit not in seen:
                    seen.add(hit)
                    # 若子适配器已返回带 source 的 MemoryHit，沿用其 source；
                    # 否则（如测试用 DictMemory 返回纯 str）按当前通道补标 source。
                    if isinstance(hit, MemoryHit) and hit.source:
                        merged.append(hit)
                    else:
                        merged.append(
                            MemoryHit(
                                str(hit),
                                score=hit.score if isinstance(hit, MemoryHit) else None,
                                source=source_label,
                                run_id=hit.run_id if isinstance(hit, MemoryHit) else None,
                            )
                        )
        return merged[:k]
