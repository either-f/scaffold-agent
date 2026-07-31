"""离线记忆巩固：把 checkpoint 里的短期情景记忆（RunState.messages）批量整理成
长期语义记忆与偏好记忆——去重、合并、提取事实、修正错误，模拟"睡眠整理记忆"。

独立离线脚本，只通过 MemoryPort.add/search 读写，不侵入运行时内核；
CheckpointStore 只读，不做任何修改。

去重：写入走各 MemoryPort 已有的 content-hash 去重（PgVectorMemory 按 namespace+role+
content_hash 唯一约束），本脚本不重复实现。
修正错误：MemoryPort 首版不提供 update/delete（见 ADR-0004），"修正"做不到覆盖旧记录，
只能让抽取 prompt 只输出最新结论、不重复写入过时的旧结论；各 adapter 检索都按时间倒序，
新结论天然排在旧结论前面——这是"软修正"，不是真删除，README 会如实注明。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.ports import MemoryPort, ModelPort
from agent_kernel.types import Message

EXTRACTION_PROMPT = """你负责离线记忆巩固：整理下面这段对话历史，做三件事——
1. 提取值得长期记住的事实（项目信息、决策、结论等，不含闲聊或工具原始输出）。
2. 提取用户的语言习惯、格式要求、风格偏好、禁忌与约束（例如"以后都用中文回复"）。
3. 如果新内容修正或推翻了更早的信息（比如用户改变了偏好），只输出修正后的最新结论，
   不要同时保留互相矛盾的旧结论。

只输出 JSON，不要输出其它任何内容：
{"facts": ["事实1", "事实2"], "preferences": ["偏好1", "偏好2"]}
没有可提取内容时输出 {"facts": [], "preferences": []}。"""


def _dialogue_text(messages: list[Message]) -> str:
    # tool 原始输出不参与巩固，跟长期记忆一贯的过滤口径一致（见 ADR-0004）
    return "\n".join(f"[{m.role}] {m.content}" for m in messages if m.role in ("user", "assistant"))


def consolidate_run(
    run_id: str,
    messages: list[Message],
    model: ModelPort,
    long_term: MemoryPort,
    preferences: MemoryPort,
) -> dict:
    dialogue = _dialogue_text(messages)
    if not dialogue.strip():
        return {"run_id": run_id, "facts_written": 0, "preferences_written": 0, "skipped": True}

    output = model.complete([Message("system", EXTRACTION_PROMPT), Message("user", dialogue)], [])
    start, end = output.text.find("{"), output.text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"巩固模型返回非法 JSON: {output.text!r}")
    parsed = json.loads(output.text[start : end + 1])

    facts = [f.strip() for f in parsed.get("facts", []) if f.strip()]
    prefs = [p.strip() for p in parsed.get("preferences", []) if p.strip()]
    for fact in facts:
        long_term.add(run_id, "assistant", fact)
    for pref in prefs:
        preferences.add(run_id, "assistant", pref)

    return {"run_id": run_id, "facts_written": len(facts), "preferences_written": len(prefs), "skipped": False}


def consolidate_all(
    checkpoint_root: str,
    model: ModelPort,
    long_term: MemoryPort,
    preferences: MemoryPort,
) -> dict:
    store = JsonCheckpointStore(checkpoint_root)
    run_ids = sorted(p.name for p in store.root.iterdir() if p.is_dir()) if store.root.exists() else []
    results = []
    for run_id in run_ids:
        state = store.load(run_id)
        if state is None:
            continue
        results.append(consolidate_run(run_id, state.messages, model, long_term, preferences))
    return {
        "runs_processed": len(results),
        "total_facts": sum(r["facts_written"] for r in results),
        "total_preferences": sum(r["preferences_written"] for r in results),
        "results": results,
    }


# --------------------------------------------------------------------------- offline
class FixedExtractionModel(ModelPort):
    """脚本化模型：为每个 run_id 返回预先写好的抽取 JSON，不调用真实 LLM。"""

    def __init__(self, scripted: dict[str, dict]) -> None:
        self.scripted = scripted
        self.calls: list[str] = []

    def complete(self, messages, tools):
        dialogue = messages[1].content
        self.calls.append(dialogue)
        for run_id, payload in self.scripted.items():
            if run_id in dialogue:
                return _model_output(payload)
        return _model_output({"facts": [], "preferences": []})


def _model_output(payload: dict):
    from agent_kernel.types import ModelOutput

    return ModelOutput(json.dumps(payload, ensure_ascii=False))


class DictMemory(MemoryPort):
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, run_id: str, role: str, content: str) -> None:
        if (run_id, role, content) in self.items:
            return
        self.items.append((run_id, role, content))

    def search(self, query: str, k: int = 5) -> list[str]:
        return [content for _, _, content in self.items[:k]]


def run_offline() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        from agent_kernel.types import RunState

        state_a = RunState(run_id="consolidate-run-a")
        state_a.add("user", "consolidate-run-a: 项目内部代号是水星 Mercury。")
        state_a.add("assistant", "好的，已记住项目代号是 Mercury。")
        store.save(state_a)

        state_b = RunState(run_id="consolidate-run-b")
        state_b.add("user", "consolidate-run-b: 以后回复都用中文，不要用表情符号。")
        state_b.add("assistant", "明白，以后只用中文回复。")
        store.save(state_b)

        state_empty = RunState(run_id="consolidate-run-empty")
        state_empty.add("tool", "some_tool: 原始输出不应参与巩固")
        store.save(state_empty)

        model = FixedExtractionModel(
            {
                "consolidate-run-a": {"facts": ["项目内部代号是 Mercury。"], "preferences": []},
                "consolidate-run-b": {"facts": [], "preferences": ["用户要求回复只用中文，不用表情符号。"]},
            }
        )
        long_term = DictMemory()
        preferences = DictMemory()

        report = consolidate_all(tmp, model, long_term, preferences)

        fact_ok = any("Mercury" in item[2] for item in long_term.items)
        pref_ok = any("只用中文" in item[2] for item in preferences.items)
        empty_skipped = any(r["run_id"] == "consolidate-run-empty" and r["skipped"] for r in report["results"])
        no_cross_contamination = not any("Mercury" in item[2] for item in preferences.items) and not any(
            "中文" in item[2] for item in long_term.items
        )

        ok = (
            report["runs_processed"] == 3
            and fact_ok
            and pref_ok
            and empty_skipped
            and no_cross_contamination
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "consolidation-offline",
            "runs_processed": report["runs_processed"],
            "total_facts": report["total_facts"],
            "total_preferences": report["total_preferences"],
            "fact_ok": fact_ok,
            "pref_ok": pref_ok,
            "empty_skipped": empty_skipped,
            "no_cross_contamination": no_cross_contamination,
            "ok": ok,
        }


# --------------------------------------------------------------------------- real
def run_real() -> dict:
    import os

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")

    from agent_kernel.adapters.memory_pgvector import PgVectorMemory
    from agent_kernel.adapters.model_litellm import LiteLLMModel
    from agent_kernel.adapters.tools_local import LocalToolbox
    from agent_kernel.kernel import AgentKernel
    from agent_kernel.planners.react import ReactPlanner
    from agent_kernel.types import RunState

    dsn = os.environ.get("AGENT_MEMORY_DSN", "postgresql://agent:agent@127.0.0.1:5432/agent_memory")

    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        chat_model = LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0)

        def kernel() -> AgentKernel:
            return AgentKernel(
                model=chat_model, tools=LocalToolbox(), planner=ReactPlanner(), checkpoints=store, max_steps=2
            )

        state_a = kernel().run(
            "请记住：这个项目内部代号叫 Aurora。只回复已记住，不要输出其它内容。",
            state=RunState(run_id="consolidation-real-fact"),
        )
        state_b = kernel().run(
            "请记住：以后回复都只用中文，禁止使用表情符号。只回复已记住，不要输出其它内容。",
            state=RunState(run_id="consolidation-real-preference"),
        )

        extraction_model = LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0)
        long_term = PgVectorMemory(dsn, "consolidation-eval-long-term", timeout=60)
        preferences = PgVectorMemory(dsn, "consolidation-eval-preferences", timeout=60)

        report = consolidate_all(tmp, extraction_model, long_term, preferences)

        fact_hits = long_term.search("这个项目的代号是什么？", k=5)
        fact_found = any("Aurora" in hit for hit in fact_hits)
        pref_hits = preferences.search("用户的语言习惯、格式要求、风格偏好、禁忌与约束", k=5)
        pref_found = any("中文" in hit for hit in pref_hits)

        ok = (
            state_a.status == "done"
            and state_b.status == "done"
            and report["runs_processed"] == 2
            and fact_found
            and pref_found
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "consolidation-real",
            "runs_processed": report["runs_processed"],
            "total_facts": report["total_facts"],
            "total_preferences": report["total_preferences"],
            "fact_found": fact_found,
            "pref_found": pref_found,
            "ok": ok,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_offline() if args.mode == "offline" else run_real()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
