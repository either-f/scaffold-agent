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

from agent_kernel.adapters.memory.graph import GraphMemory
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.ports import MemoryPort, ModelPort
from agent_kernel.types import Message

GRAPH_NAMESPACE = "default"
REL_HAS_FACT = "HAS_FACT"
REL_HAS_PREFERENCE = "HAS_PREFERENCE"
REL_FOLLOWS = "FOLLOWS"  # 同一 run 内事实的抽取顺序＝演化链
REL_SIMILAR_TO = "SIMILAR_TO"
SIMILARITY_THRESHOLD = 0.5  # ponytail: 词袋 Jaccard 近似语义关联；误判/漏判换成
# embedding 余弦相似度（PgVectorMemory 已有 embedding，接进来即可）再收紧阈值


def _shingles(s: str) -> set[str]:
    # ponytail: 中文无空格分词，退化用字符 2-gram 做词袋；英文长词一样能命中。
    # 短句(<2 字符)整句当一个 shingle，避免空集合。
    return {s[i : i + 2] for i in range(len(s) - 1)} or {s}


def _jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _record_graph(graph: GraphMemory, run_id: str, facts: list[str], prefs: list[str]) -> None:
    """Graph-Aware Consolidation：facts/preferences 除了写进 long_term/preferences，
    还落进图谱——挂到 user 节点上（画像沉淀），并维护 FOLLOWS（同 run 内演化顺序）与
    SIMILAR_TO（跟历史已有事实的词袋相似度）两类关系。"""
    user_node = f"user:{run_id}"
    prev_fact: str | None = None
    existing = graph.search_edges(GRAPH_NAMESPACE, relation=REL_HAS_FACT, k=1000)
    existing_facts = [obj for _, _, obj in existing]
    for fact in facts:
        graph.add(run_id, "assistant", fact)
        graph.add_edge(GRAPH_NAMESPACE, user_node, REL_HAS_FACT, fact)
        if prev_fact is not None and prev_fact != fact:
            graph.add_edge(GRAPH_NAMESPACE, prev_fact, REL_FOLLOWS, fact)
        for old_fact in existing_facts:
            if old_fact != fact and _jaccard(old_fact, fact) >= SIMILARITY_THRESHOLD:
                graph.add_edge(GRAPH_NAMESPACE, fact, REL_SIMILAR_TO, old_fact)
        existing_facts.append(fact)
        prev_fact = fact
    for pref in prefs:
        graph.add(run_id, "assistant", pref)
        graph.add_edge(GRAPH_NAMESPACE, user_node, REL_HAS_PREFERENCE, pref)


def build_profile(graph: GraphMemory, run_id: str) -> dict[str, list[str]]:
    """长期用户画像沉淀：从图谱读回某 run（代理 user，见 ADR-0004 无独立 user_id 概念）
    挂着的全部 HAS_FACT / HAS_PREFERENCE，按关系分组即画像。"""
    neighbors = graph.get_neighbors(GRAPH_NAMESPACE, f"user:{run_id}", direction="outgoing", k=1000)
    profile: dict[str, list[str]] = {"facts": [], "preferences": []}
    for _subject, relation, obj, _direction in neighbors:
        if relation == REL_HAS_FACT:
            profile["facts"].append(obj)
        elif relation == REL_HAS_PREFERENCE:
            profile["preferences"].append(obj)
    return profile

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
    graph: GraphMemory | None = None,
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
    if graph is not None:
        _record_graph(graph, run_id, facts, prefs)

    return {"run_id": run_id, "facts_written": len(facts), "preferences_written": len(prefs), "skipped": False}


def consolidate_all(
    checkpoint_root: str,
    model: ModelPort,
    long_term: MemoryPort,
    preferences: MemoryPort,
    graph: GraphMemory | None = None,
) -> dict:
    store = JsonCheckpointStore(checkpoint_root)
    run_ids = sorted(p.name for p in store.root.iterdir() if p.is_dir()) if store.root.exists() else []
    results = []
    for run_id in run_ids:
        state = store.load(run_id)
        if state is None:
            continue
        results.append(consolidate_run(run_id, state.messages, model, long_term, preferences, graph))
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
                "consolidate-run-a": {
                    "facts": ["项目内部代号是 Mercury。", "项目内部代号改叫 Mercury 二期。"],
                    "preferences": [],
                },
                "consolidate-run-b": {"facts": [], "preferences": ["用户要求回复只用中文，不用表情符号。"]},
            }
        )
        long_term = DictMemory()
        preferences = DictMemory()
        graph = GraphMemory()

        report = consolidate_all(tmp, model, long_term, preferences, graph)

        fact_ok = any("Mercury" in item[2] for item in long_term.items)
        pref_ok = any("只用中文" in item[2] for item in preferences.items)
        empty_skipped = any(r["run_id"] == "consolidate-run-empty" and r["skipped"] for r in report["results"])
        no_cross_contamination = not any("Mercury" in item[2] for item in preferences.items) and not any(
            "中文" in item[2] for item in long_term.items
        )

        profile = build_profile(graph, "consolidate-run-a")
        profile_ok = any("Mercury" in f for f in profile["facts"])
        follows_ok = bool(
            graph.search_edges(GRAPH_NAMESPACE, relation=REL_FOLLOWS, k=10)
        )
        similar_ok = bool(
            graph.search_edges(GRAPH_NAMESPACE, relation=REL_SIMILAR_TO, k=10)
        )

        ok = (
            report["runs_processed"] == 3
            and fact_ok
            and pref_ok
            and empty_skipped
            and no_cross_contamination
            and profile_ok
            and follows_ok
            and similar_ok
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
            "profile_ok": profile_ok,
            "follows_ok": follows_ok,
            "similar_ok": similar_ok,
            "ok": ok,
        }


# --------------------------------------------------------------------------- real
def run_real() -> dict:
    import os

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")

    from agent_kernel.adapters.memory.pgvector import PgVectorMemory
    from agent_kernel.adapters.model.litellm import LiteLLMModel
    from agent_kernel.adapters.tools.local import LocalToolbox
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
        graph = GraphMemory(str(Path(tmp) / "graph.sqlite3"))

        report = consolidate_all(tmp, extraction_model, long_term, preferences, graph)

        fact_hits = long_term.search("这个项目的代号是什么？", k=5)
        fact_found = any("Aurora" in hit for hit in fact_hits)
        pref_hits = preferences.search("用户的语言习惯、格式要求、风格偏好、禁忌与约束", k=5)
        pref_found = any("中文" in hit for hit in pref_hits)
        profile_found = any(
            "Aurora" in f for f in build_profile(graph, "consolidation-real-fact")["facts"]
        )

        ok = (
            state_a.status == "done"
            and state_b.status == "done"
            and report["runs_processed"] == 2
            and fact_found
            and pref_found
            and profile_found
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "consolidation-real",
            "runs_processed": report["runs_processed"],
            "total_facts": report["total_facts"],
            "total_preferences": report["total_preferences"],
            "fact_found": fact_found,
            "pref_found": pref_found,
            "profile_found": profile_found,
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
