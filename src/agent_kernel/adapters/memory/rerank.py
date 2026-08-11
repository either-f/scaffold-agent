"""RerankedMemory：inner.search() 先多召回一批候选（over_fetch 倍），再用
rerank_fn 精排取前 k——对齐 FoxChat"BM25/向量粗召回→Cross-Encoder 精排"两段式。

rerank_fn 是纯函数 (query, candidates) -> reordered_candidates，跟具体精排
模型解耦：可以传 flashrank_rerank_fn() 接真模型，也可以传测试里的假函数，
RerankedMemory 本身不 import 任何第三方库。

安装：uv sync --extra rerank   （或 pip install flashrank，只有调用
flashrank_rerank_fn() 时才需要，import 这个文件本身不需要）
"""
from __future__ import annotations

from typing import Callable

from ...ports import MemoryPort

RerankFn = Callable[[str, list[str]], list[str]]


class RerankedMemory(MemoryPort):
    def __init__(self, inner: MemoryPort, rerank_fn: RerankFn, over_fetch: int = 3) -> None:
        if over_fetch < 1:
            raise ValueError("over_fetch 必须 >= 1")
        self.inner = inner
        self.rerank_fn = rerank_fn
        self.over_fetch = over_fetch

    def add(self, run_id: str, role: str, content: str) -> None:
        self.inner.add(run_id, role, content)

    def search(self, query: str, k: int = 5) -> list[str]:
        candidates = self.inner.search(query, k=k * self.over_fetch)
        if not candidates:
            return candidates
        return self.rerank_fn(query, candidates)[:k]


def flashrank_rerank_fn(model_name: str = "ms-marco-MiniLM-L-12-v2") -> RerankFn:
    """FlashRank Cross-Encoder 精排（对齐 FoxChat 用的同款模型）。返回值是
    RerankFn，直接传给 RerankedMemory 的 rerank_fn 参数。"""
    try:
        from flashrank import Ranker, RerankRequest
    except ImportError as exc:  # 融合纪律：第三方依赖只在 adapter 内出现
        raise ImportError("需要 flashrank：uv sync --extra rerank") from exc

    ranker = Ranker(model_name=model_name)

    def rerank_fn(query: str, candidates: list[str]) -> list[str]:
        passages = [{"id": i, "text": c} for i, c in enumerate(candidates)]
        results = ranker.rerank(RerankRequest(query=query, passages=passages))
        return [candidates[r["id"]] for r in results]

    return rerank_fn
