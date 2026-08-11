"""flashrank_rerank_fn 集成测试：先用 monkeypatch 替掉 Ranker.rerank 本身验证
参数组装、结果按 id 映回原始 candidates 顺序都正确（不依赖真实模型质量）；最后
一个测试跑真实 Ranker + 默认模型（flashrank 默认模型随包分发，不联网），验证
"语义相关的候选排到前面"这个真实效果，不再是纯接口对齐。跳过：本机未装
flashrank（uv sync --extra rerank）时。

运行：PYTHONPATH=src python3 -m pytest tests/test_rerank_flashrank_real.py
"""
import sys

sys.path.insert(0, "src")

import pytest

flashrank = pytest.importorskip("flashrank")

from agent_kernel.adapters.memory.rerank import RerankedMemory, flashrank_rerank_fn
from agent_kernel.ports import MemoryPort


class FixedMemory(MemoryPort):
    def __init__(self, items: list[str]) -> None:
        self.items = items

    def add(self, run_id, role, content):
        self.items.append(content)

    def search(self, query: str, k: int = 5) -> list[str]:
        return self.items[:k]


def test_flashrank_rerank_fn_reorders_candidates_by_fake_scores(monkeypatch):
    captured_requests = []

    class FakeRanker:
        def __init__(self, model_name=None):
            self.model_name = model_name

        def rerank(self, request):
            captured_requests.append(request)
            # 故意打乱分数顺序：id=2 最相关，其次 id=0，最后 id=1
            return [
                {"id": 2, "text": request.passages[2]["text"], "score": 0.9},
                {"id": 0, "text": request.passages[0]["text"], "score": 0.5},
                {"id": 1, "text": request.passages[1]["text"], "score": 0.1},
            ]

    monkeypatch.setattr(flashrank, "Ranker", FakeRanker)

    rerank_fn = flashrank_rerank_fn(model_name="fake-model")
    result = rerank_fn("query", ["doc-a", "doc-b", "doc-c"])

    assert result == ["doc-c", "doc-a", "doc-b"]  # 按 id 映回原始文本，顺序按 fake 分数排
    assert captured_requests[0].query == "query"


def test_flashrank_rerank_fn_integrates_with_reranked_memory(monkeypatch):
    class FakeRanker:
        def __init__(self, model_name=None):
            pass

        def rerank(self, request):
            # 原样倒序返回，验证 RerankedMemory 的 over_fetch + k 截断跟 flashrank 路径拼得上
            return [
                {"id": i, "text": p["text"], "score": 1.0}
                for i, p in reversed(list(enumerate(request.passages)))
            ]

    monkeypatch.setattr(flashrank, "Ranker", FakeRanker)

    inner = FixedMemory(["a", "b", "c", "d"])
    rerank_fn = flashrank_rerank_fn()
    mem = RerankedMemory(inner, rerank_fn, over_fetch=2)
    # over_fetch=2, k=2 → inner.search(k=4) 拿到全部 4 条，fake ranker 整体倒序后取前 2
    assert mem.search("q", k=2) == ["d", "c"]


def test_real_ranker_puts_semantically_relevant_passage_first():
    rerank_fn = flashrank_rerank_fn()  # 真实 Ranker，不 monkeypatch
    result = rerank_fn(
        "what is the capital of france",
        [
            "Bananas are a good source of potassium.",
            "The Eiffel Tower is a famous landmark in Paris.",
            "Paris is the capital and most populous city of France.",
        ],
    )
    assert result[0] == "Paris is the capital and most populous city of France."
    assert result[-1] == "Bananas are a good source of potassium."


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
