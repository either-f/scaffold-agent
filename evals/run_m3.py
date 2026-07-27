"""M3 记忆与上下文 eval；M3A 先提供真实语义记忆子命令。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_pgvector import PgVectorMemory

FACTS = [
    ("用户偏好使用中文交流。", "应该用哪种语言回答用户？", "中文"),
    ("项目内部代号是水星 Mercury。", "这个项目的代号叫什么？", "Mercury"),
    ("M3 的数据库选择是 PostgreSQL 和 pgvector。", "长期记忆使用什么数据库？", "pgvector"),
    ("默认 embedding 模型是 text-embedding-v4，维度为 1024。", "向量模型默认输出多少维？", "1024"),
    ("M3 首版采用精确余弦检索，不创建 HNSW 索引。", "首版会建立近似向量索引吗？", "HNSW"),
]


def run_memory() -> dict:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    dsn = os.environ.get(
        "AGENT_MEMORY_DSN",
        "postgresql://agent:agent@127.0.0.1:5432/agent_memory",
    )
    memory = PgVectorMemory(dsn, "m3-eval", timeout=60)
    isolated = PgVectorMemory(dsn, "m3-eval-isolated", timeout=60)

    for content, _, _ in FACTS:
        memory.add("session-a", "user", content)
    memory.add("session-b", "user", FACTS[0][0])
    memory.add("session-a", "tool", "这条工具结果不得进入长期记忆。")

    results = []
    for _, query, keyword in FACTS:
        hits = memory.search(query, k=3)
        passed = any(keyword.casefold() in hit.casefold() for hit in hits)
        results.append({"query": query, "keyword": keyword, "hits": hits, "passed": passed})

    all_hits = memory.search("用户的语言偏好是什么？", k=10)
    duplicate_ok = sum(hit == FACTS[0][0] for hit in all_hits) == 1
    tool_ignored = all("工具结果不得进入" not in hit for hit in memory.search("工具结果", k=10))
    namespace_isolated = isolated.search("项目代号是什么？", k=3) == []
    passed = sum(result["passed"] for result in results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "memory",
        "model": "dashscope/text-embedding-v4",
        "dimensions": 1024,
        "passed": passed,
        "total": len(results),
        "duplicate_ok": duplicate_ok,
        "tool_ignored": tool_ignored,
        "namespace_isolated": namespace_isolated,
        "results": results,
        "ok": passed >= 4 and duplicate_ok and tool_ignored and namespace_isolated,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="suite", required=True)
    memory_parser = subparsers.add_parser("memory", help="真实 PostgreSQL + DashScope 记忆 eval")
    memory_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_memory()
    for result in report["results"]:
        marker = "PASS" if result["passed"] else "FAIL"
        print(f"[{marker}] {result['query']} -> {result['hits']}")
    print(
        f"M3 memory: {report['passed']}/{report['total']} "
        f"dedupe={report['duplicate_ok']} tool_ignored={report['tool_ignored']} "
        f"isolated={report['namespace_isolated']}"
    )
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
