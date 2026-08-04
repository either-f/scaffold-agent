"""CompositeMemory 离线 eval：验证写路径只落 episodic、查询按 semantic>graph>episodic
优先级合并去重。纯逻辑测试，用内存字典 Fake，不需要真实数据库。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory.composite import CompositeMemory
from agent_kernel.ports import MemoryPort


class DictMemory(MemoryPort):
    def __init__(self, canned: list[str] | None = None) -> None:
        self.added: list[tuple[str, str, str]] = []
        self.canned = canned or []

    def add(self, run_id: str, role: str, content: str) -> None:
        self.added.append((run_id, role, content))

    def search(self, query: str, k: int = 5) -> list[str]:
        return self.canned[:k]


def run_composite() -> dict:
    episodic = DictMemory(canned=["原始消息：今天天气不错", "重复项"])
    semantic = DictMemory(canned=["提炼事实：项目代号是 Mercury", "重复项"])
    graph = DictMemory(canned=["关系：agent-kernel uses Neo4j"])

    memory = CompositeMemory(episodic, semantic, graph, episodic_k=5, semantic_k=5, graph_k=5)

    memory.add("run-1", "user", "今天天气不错")
    write_only_episodic = (
        len(episodic.added) == 1
        and episodic.added[0] == ("run-1", "user", "今天天气不错")
        and semantic.added == []
        and graph.added == []
    )

    hits = memory.search("项目代号是什么？", k=10)
    priority_order = hits[:3] == [
        "提炼事实：项目代号是 Mercury",
        "重复项",
        "关系：agent-kernel uses Neo4j",
    ]
    dedup_ok = hits.count("重复项") == 1
    episodic_fallback_included = "原始消息：今天天气不错" in hits

    no_graph_memory = CompositeMemory(episodic, semantic, graph=None)
    no_graph_hits = no_graph_memory.search("x", k=10)
    graph_optional_ok = "关系：agent-kernel uses Neo4j" not in no_graph_hits

    ok = (
        write_only_episodic
        and priority_order
        and dedup_ok
        and episodic_fallback_included
        and graph_optional_ok
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "composite-memory",
        "write_only_episodic": write_only_episodic,
        "priority_order": priority_order,
        "dedup_ok": dedup_ok,
        "episodic_fallback_included": episodic_fallback_included,
        "graph_optional_ok": graph_optional_ok,
        "ok": ok,
    }


def main() -> int:
    report = run_composite()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
