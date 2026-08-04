"""M6 真实 Neo4j 图记忆 eval：需要 compose.yaml 的 neo4j 服务已启动。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

EDGES = [
    ("agent-kernel", "uses", "Neo4j"),
    ("agent-kernel", "uses", "pgvector"),
    ("Neo4j", "stores", "三元组"),
]


def run_graph() -> dict:
    from agent_kernel.adapters.memory.graph_neo4j import Neo4jGraphMemory

    import os

    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "agent-kernel")

    memory = Neo4jGraphMemory(uri, user, password, namespace="m6-graph-eval")
    isolated = Neo4jGraphMemory(uri, user, password, namespace="m6-graph-eval-isolated")

    try:
        memory.add("run-1", "user", "项目内部代号是水星 Mercury。")
        memory.add("run-1", "user", "项目内部代号是水星 Mercury。")  # 重复写入验证去重
        memory.add("run-1", "tool", "工具结果不得进入长期记忆。")

        for subject, relation, object_ in EDGES:
            memory.add_edge(subject, relation, object_)
            memory.add_edge(subject, relation, object_)  # 重复写入验证去重

        fact_hits = memory.search("项目内部代号", k=5)
        fact_found = any("Mercury" in hit for hit in fact_hits)
        fact_dedup_ok = sum("Mercury" in hit for hit in fact_hits) == 1
        tool_ignored = all("工具结果不得进入" not in hit for hit in memory.search("工具结果", k=5))

        edge_hits = memory.search_edges(subject="agent-kernel")
        edge_dedup_ok = len(edge_hits) == 2
        relation_filtered = memory.search_edges(subject="agent-kernel", object="Neo4j")
        relation_ok = relation_filtered == [("agent-kernel", "uses", "Neo4j")]

        neighbors = memory.get_neighbors("agent-kernel", direction="outgoing")
        neighbor_ok = {(s, r, o) for s, r, o, _ in neighbors} == {
            ("agent-kernel", "uses", "Neo4j"),
            ("agent-kernel", "uses", "pgvector"),
        }
        incoming = memory.get_neighbors("Neo4j", direction="incoming")
        incoming_ok = len(incoming) == 1 and incoming[0][3] == "incoming"

        namespace_isolated = isolated.search("项目内部代号", k=5) == [] and isolated.search_edges() == []

        ok = (
            fact_found
            and fact_dedup_ok
            and tool_ignored
            and edge_dedup_ok
            and relation_ok
            and neighbor_ok
            and incoming_ok
            and namespace_isolated
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "graph",
            "backend": "neo4j",
            "fact_found": fact_found,
            "fact_dedup_ok": fact_dedup_ok,
            "tool_ignored": tool_ignored,
            "edge_dedup_ok": edge_dedup_ok,
            "relation_ok": relation_ok,
            "neighbor_ok": neighbor_ok,
            "incoming_ok": incoming_ok,
            "namespace_isolated": namespace_isolated,
            "ok": ok,
        }
    finally:
        with memory.driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.namespace STARTS WITH 'm6-graph-eval' DETACH DELETE n"
            )
        memory.close()
        isolated.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_graph()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
