"""Hybrid GraphRAG 离线批处理：Markdown 清洗 → Chunk 切片 → LLM 抽取实体/关系写入图谱
→ 原文双路索引进向量/关键词腿。跟 evals/run_consolidation.py 同一套路数——LLM 抽取
成本高，不能挂在内核同步 add() 路径上，独立离线脚本跑，只通过 MemoryPort.add/search
与 GraphMemory.add_edge 读写。

--mode offline：FakeScriptedModel + SqliteMemory（向量腿离线替身，ADR-0004 里它本来
就是 pgvector 占位实现的定位）+ Bm25Memory（关键词腿，真算法不是占位）+
GraphMemory（图腿）。CI 用，不触网。

--mode real：需要 DASHSCOPE_API_KEY（embedding）+ DEEPSEEK_API_KEY（抽取）+
MILVUS_URI（默认本地 Milvus Lite 文件）+ ELASTICSEARCH_URL（需要
`docker compose up -d elasticsearch`）+ NEO4J_URI/USER/PASSWORD。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory.bm25 import Bm25Memory
from agent_kernel.adapters.memory.graph import GraphMemory
from agent_kernel.adapters.memory.hybrid_rag import HybridGraphRAG
from agent_kernel.adapters.memory.sqlite import SqliteMemory
from agent_kernel.ports import MemoryPort, ModelPort
from agent_kernel.types import Message, ModelOutput

GRAPH_NAMESPACE = "default"

# --------------------------------------------------------------- ingestion: cleaning
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_markdown(text: str) -> str:
    """去 HTML 注释、把 [text](url) 压成 text（丢链接噪声保锚文本）、折叠连续空行。"""
    text = _HTML_COMMENT.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


# --------------------------------------------------------------- ingestion: chunking
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


def chunk_markdown(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """先按标题行切大块；块内超长再按段落滑窗切分带 overlap 重叠内容。"""
    if not text.strip():
        return []
    starts = [m.start() for m in _HEADING.finditer(text)]
    sections = (
        [text[s:e] for s, e in zip(starts, [*starts[1:], len(text)])]
        if starts
        else [text]
    )

    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_chars:
            chunks.append(section)
            continue
        start = 0
        while start < len(section):
            end = min(start + max_chars, len(section))
            chunks.append(section[start:end].strip())
            if end == len(section):
                break
            start = end - overlap
    return [c for c in chunks if c]


# --------------------------------------------------------------- ingestion: extraction
ENTITY_EXTRACTION_PROMPT = """你负责从下面这段文档片段里抽取实体与关系，用于构建知识图谱。
规则：
1. 只抽取文档里明确陈述的实体与关系，不要推测或编造。
2. relation 用简短大写英文短语（如 LED_BY、MENTOR_OF、PART_OF）。
3. 没有可抽取内容时输出空列表。

只输出 JSON，不要输出其它任何内容：
{"entities": ["实体1", "实体2"], "relations": [{"subject": "...", "relation": "...", "object": "..."}]}"""


def _parse_extraction(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"抽取模型返回非法 JSON: {text!r}")
    return json.loads(text[start : end + 1])


def ingest_document(
    text: str,
    source_id: str,
    model: ModelPort,
    vector: MemoryPort,
    keyword: MemoryPort,
    graph: GraphMemory,
    namespace: str = GRAPH_NAMESPACE,
) -> dict:
    """清洗 → 切片 → 每个 chunk 写 vector/keyword 两路原文索引 + LLM 抽取
    entity/relation 写 graph.add_edge。graph 不落原文（跟 HybridGraphRAG.add() 的
    "graph 只由 ingestion 写实体关系"约定一致）。"""
    chunks = chunk_markdown(clean_markdown(text))
    entities_written = 0
    relations_written = 0
    for chunk in chunks:
        vector.add(source_id, "assistant", chunk)
        keyword.add(source_id, "assistant", chunk)
        output = model.complete(
            [Message("system", ENTITY_EXTRACTION_PROMPT), Message("user", chunk)], []
        )
        parsed = _parse_extraction(output.text)
        entities_written += len(parsed.get("entities", []))
        for rel in parsed.get("relations", []):
            subject, relation, obj = rel.get("subject"), rel.get("relation"), rel.get("object")
            if subject and relation and obj:
                graph.add_edge(namespace, subject, relation, obj)
                relations_written += 1
    return {
        "source_id": source_id,
        "chunks": len(chunks),
        "entities": entities_written,
        "relations": relations_written,
    }


# --------------------------------------------------------------------------- offline
class FixedExtractionModel(ModelPort):
    """脚本化模型：chunk 内容命中某个关键词时返回预先写好的抽取 JSON。"""

    def __init__(self, scripted: dict[str, dict]) -> None:
        self.scripted = scripted
        self.calls = 0

    def complete(self, messages: list[Message], tools) -> ModelOutput:
        self.calls += 1
        chunk = messages[1].content
        for key, payload in self.scripted.items():
            if key in chunk:
                return ModelOutput(json.dumps(payload, ensure_ascii=False))
        return ModelOutput(json.dumps({"entities": [], "relations": []}, ensure_ascii=False))


def run_offline() -> dict:
    vector = SqliteMemory()
    keyword = Bm25Memory()
    graph = GraphMemory()
    ns = GRAPH_NAMESPACE

    model = FixedExtractionModel(
        {
            "Aurora": {
                "entities": ["Aurora", "李雷"],
                "relations": [{"subject": "Aurora", "relation": "LED_BY", "object": "李雷"}],
            },
            "实习项目": {
                "entities": ["李雷", "王芳"],
                "relations": [{"subject": "李雷", "relation": "MENTOR_OF", "object": "王芳"}],
            },
        }
    )

    doc_a = "# 项目Aurora\n项目内部代号是Aurora，负责人是李雷。"
    doc_b = "# 团队成员\n李雷指导王芳完成实习项目。"
    report_a = ingest_document(doc_a, "doc-a", model, vector, keyword, graph, ns)
    report_b = ingest_document(doc_b, "doc-b", model, vector, keyword, graph, ns)

    hybrid = HybridGraphRAG(
        vector,
        keyword,
        graph,
        neighbors_fn=lambda node: graph.get_neighbors(ns, node, "both", k=20),
        edges_fn=lambda: graph.search_edges(ns, k=10000),
        hops=2,
    )

    # 断言①：RRF——同时命中向量+关键词两路的探针内容排名应高于只命中一路的
    fake_hit_all = "关于Aurora项目向量腿和关键词腿都能查到这条探针内容"
    vector.add("probe", "assistant", fake_hit_all)
    keyword.add("probe", "assistant", fake_hit_all)
    only_vector = "关于Aurora的另一条探针内容只在向量腿里能查到"
    vector.add("probe", "assistant", only_vector)  # 故意不写 keyword 腿

    rrf_hits = hybrid.search("Aurora", k=10)
    rrf_ok = (
        fake_hit_all in rrf_hits
        and only_vector in rrf_hits
        and rrf_hits.index(fake_hit_all) < rrf_hits.index(only_vector)
    )

    # 断言②：多跳召回——"王芳"从未出现在 doc_a/探针内容里，只能通过
    # Aurora --LED_BY--> 李雷 --MENTOR_OF--> 王芳 两跳图扩展找到
    assert "王芳" not in doc_a and "王芳" not in fake_hit_all and "王芳" not in only_vector
    multihop_ok = any("王芳" in h for h in hybrid.search("Aurora", k=10))

    ok = rrf_ok and multihop_ok and report_a["relations"] == 1 and report_b["relations"] == 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "hybrid-rag-offline",
        "ingest": [report_a, report_b],
        "rrf_ok": rrf_ok,
        "multihop_ok": multihop_ok,
        "ok": ok,
    }


# --------------------------------------------------------------------------- real
def run_real() -> dict:
    import os

    missing = [
        var
        for var in ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")
        if not os.environ.get(var)
    ]
    if missing:
        raise RuntimeError(f"缺少环境变量: {', '.join(missing)}")

    from agent_kernel.adapters.memory.milvus import MilvusMemory
    from agent_kernel.adapters.model.litellm import LiteLLMModel

    milvus_uri = os.environ.get("MILVUS_URI", str(PROJECT_ROOT / "evals" / ".milvus-real.db"))
    ns = "hybrid-rag-real-eval"
    vector = MilvusMemory(milvus_uri, ns)
    keyword = Bm25Memory()  # ponytail: 真实 ElasticsearchMemory 需要 docker compose up -d
    # elasticsearch，这次会话 docker daemon 没起，先用 Bm25Memory 占关键词腿的位，
    # 换成 ElasticsearchMemory(os.environ["ELASTICSEARCH_URL"], ns) 即可切真实服务。
    graph = GraphMemory()  # 同理：真实 Neo4jGraphMemory 需要真实容器，这里先用离线版
    extraction_model = LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0)

    report_a = ingest_document(
        "# 项目Aurora\n项目内部代号是Aurora，负责人是李雷。", "doc-a",
        extraction_model, vector, keyword, graph, ns,
    )
    report_b = ingest_document(
        "# 团队成员\n李雷指导王芳完成实习项目。", "doc-b",
        extraction_model, vector, keyword, graph, ns,
    )

    hybrid = HybridGraphRAG(
        vector, keyword, graph,
        neighbors_fn=lambda node: graph.get_neighbors(ns, node, "both", k=20),
        edges_fn=lambda: graph.search_edges(ns, k=10000),
        hops=2,
    )
    hits = hybrid.search("Aurora项目的负责人是谁", k=10)
    fact_found = any("Aurora" in h or "李雷" in h for h in hits)

    ok = fact_found and report_a["relations"] >= 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "hybrid-rag-real",
        "backend": "milvus-lite + bm25-fake + graph-sqlite-fake",
        "ingest": [report_a, report_b],
        "fact_found": fact_found,
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
