"""M3 真实语义记忆、离线上下文与跨会话 eval。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.adapters.tools_offload import OffloadingToolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import ModelPort
from agent_kernel.types import Message, ModelOutput, ToolSpec

FACTS = [
    ("用户偏好使用中文交流。", "应该用哪种语言回答用户？", "中文"),
    ("项目内部代号是水星 Mercury。", "这个项目的代号叫什么？", "Mercury"),
    ("M3 的数据库选择是 PostgreSQL 和 pgvector。", "长期记忆使用什么数据库？", "pgvector"),
    ("默认 embedding 模型是 text-embedding-v4，维度为 1024。", "向量模型默认输出多少维？", "1024"),
    ("M3 首版采用精确余弦检索，不创建 HNSW 索引。", "首版会建立近似向量索引吗？", "HNSW"),
]


def run_memory() -> dict:
    from agent_kernel.adapters.memory_pgvector import PgVectorMemory

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


class ContextEvalModel(ModelPort):
    def __init__(self) -> None:
        self.prompt_chars: list[int] = []
        self.summary_calls = 0

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        if messages[0].content.startswith("你负责增量压缩"):
            self.summary_calls += 1
            return ModelOutput(f"增量摘要第 {self.summary_calls} 次：保留既有目标和偏好。")
        self.prompt_chars.append(sum(len(message.content) for message in messages))
        return ModelOutput('{"thought": "完成本轮", "final": "已记录"}')


def run_context() -> dict:
    model = ContextEvalModel()
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(str(Path(tmp) / "runs"))
        kernel = AgentKernel(
            model=model,
            tools=LocalToolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
        )
        state = None
        for turn in range(1, 51):
            message = f"第 {turn} 轮：记住这一轮。" + (chr(0x4E00 + turn) * 1200)
            state = kernel.run(message, state)

        assert state is not None
        checkpoint_ok = (
            (store.root / state.run_id / "turn_050_step_001.json").exists()
            and store.load(state.run_id).turn == 50  # type: ignore[union-attr]
        )

        raw = "BEGIN-" + ("长结果" * 5000) + "-END"
        tools = LocalToolbox()
        tools.register("large", "返回长文本", lambda: raw)
        artifact_dir = Path(tmp) / "artifacts"
        offloaded = OffloadingToolbox(
            tools,
            artifact_dir=str(artifact_dir),
            max_inline_chars=100,
            preview_chars=20,
        ).call("large", {})
        artifact = artifact_dir / f"{hashlib.sha256(raw.encode('utf-8')).hexdigest()}.txt"
        artifact_ok = (
            artifact.read_text(encoding="utf-8") == raw
            and str(artifact.resolve()) in offloaded.content
            and f"字符数: {len(raw)}" in offloaded.content
            and any(a.uri == str(artifact.resolve()) for a in offloaded.artifacts)
        )

        max_chars = max(model.prompt_chars)
        ok = (
            state.status == "done"
            and state.turn == 50
            and model.summary_calls >= 2
            and state.summarized_message_count > 0
            and len(state.context_summary) <= 2000
            and max_chars <= 24000
            and checkpoint_ok
            and artifact_ok
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "context",
            "turns": state.turn,
            "summary_calls": model.summary_calls,
            "summarized_message_count": state.summarized_message_count,
            "max_prompt_chars": max_chars,
            "checkpoint_ok": checkpoint_ok,
            "artifact_ok": artifact_ok,
            "ok": ok,
        }


def run_cross_session() -> dict:
    from agent_kernel.adapters.memory_pgvector import PgVectorMemory
    from agent_kernel.adapters.model_litellm import LiteLLMModel

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    dsn = os.environ.get(
        "AGENT_MEMORY_DSN",
        "postgresql://agent:agent@127.0.0.1:5432/agent_memory",
    )
    memory = PgVectorMemory(dsn, "m3-cross-session", timeout=60)
    tools = LocalToolbox()

    def kernel() -> AgentKernel:
        return AgentKernel(
            model=LiteLLMModel(
                "deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0
            ),
            tools=tools,
            planner=ReactPlanner(),
            memory=memory,
            max_steps=3,
        )

    first = kernel().run("请记住：项目代号是 Mercury，回答语言偏好是中文。只回复已记住。")
    second = kernel().run("之前保存的项目代号和回答语言偏好分别是什么？")
    answer = second.answer or ""
    return {
        "first_status": first.status,
        "second_status": second.status,
        "answer": answer,
        "keywords": {"Mercury": "mercury" in answer.casefold(), "中文": "中文" in answer},
        "ok": first.status == "done" and second.status == "done" and "mercury" in answer.casefold() and "中文" in answer,
    }


def run_all() -> dict:
    memory = run_memory()
    context = run_context()
    cross_session = run_cross_session()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "all",
        "memory": memory,
        "context": context,
        "cross_session": cross_session,
        "ok": memory["ok"] and context["ok"] and cross_session["ok"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="suite", required=True)
    memory_parser = subparsers.add_parser("memory", help="真实 PostgreSQL + DashScope 记忆 eval")
    memory_parser.add_argument("--output", type=Path)
    context_parser = subparsers.add_parser("context", help="离线 50 轮上下文 eval")
    context_parser.add_argument("--output", type=Path)
    all_parser = subparsers.add_parser("all", help="运行全部 M3 eval")
    all_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = {
        "memory": run_memory,
        "context": run_context,
        "all": run_all,
    }[args.suite]()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
