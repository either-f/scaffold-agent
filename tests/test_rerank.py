"""RerankedMemory 单测：over_fetch 倍召回、精排改变顺序、k 截断、空结果直通、
flashrank 未安装时给出清晰报错（不测真模型，CI 不装可选依赖）。

运行：PYTHONPATH=src python3 tests/test_rerank.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.memory.rerank import RerankedMemory, flashrank_rerank_fn
from agent_kernel.ports import MemoryPort


class FixedMemory(MemoryPort):
    def __init__(self, items: list[str]) -> None:
        self.items = items
        self.search_ks: list[int] = []

    def add(self, run_id: str, role: str, content: str) -> None:
        self.items.append(content)

    def search(self, query: str, k: int = 5) -> list[str]:
        self.search_ks.append(k)
        return self.items[:k]


def _reverse_rerank(query: str, candidates: list[str]) -> list[str]:
    return list(reversed(candidates))


def test_over_fetch_requests_k_times_multiplier_from_inner():
    inner = FixedMemory([f"doc{i}" for i in range(20)])
    mem = RerankedMemory(inner, _reverse_rerank, over_fetch=3)
    mem.search("q", k=5)
    assert inner.search_ks == [15]


def test_rerank_fn_changes_order_and_result_truncated_to_k():
    inner = FixedMemory(["a", "b", "c", "d", "e", "f"])
    mem = RerankedMemory(inner, _reverse_rerank, over_fetch=2)
    # over_fetch=2, k=3 → inner.search(k=6) 拿到全部 6 条，反转后取前 3
    result = mem.search("q", k=3)
    assert result == ["f", "e", "d"]


def test_empty_inner_result_short_circuits_without_calling_rerank_fn():
    inner = FixedMemory([])
    calls = []

    def spy_rerank(query, candidates):
        calls.append(candidates)
        return candidates

    mem = RerankedMemory(inner, spy_rerank)
    assert mem.search("q", k=5) == []
    assert calls == []  # 空结果不该去调 rerank_fn


def test_add_forwards_to_inner():
    inner = FixedMemory([])
    mem = RerankedMemory(inner, _reverse_rerank)
    mem.add("r1", "user", "hello")
    assert inner.items == ["hello"]


def test_invalid_over_fetch_rejected():
    inner = FixedMemory([])
    try:
        RerankedMemory(inner, _reverse_rerank, over_fetch=0)
        assert False, "over_fetch < 1 应该拒绝"
    except ValueError:
        pass


def test_flashrank_rerank_fn_raises_clear_error_without_dependency():
    try:
        import flashrank  # noqa: F401

        return  # 环境里装了 flashrank，跳过这个"未安装"场景的断言
    except ImportError:
        pass
    try:
        flashrank_rerank_fn()
        assert False, "未安装 flashrank 时应该抛 ImportError"
    except ImportError as exc:
        assert "flashrank" in str(exc)


if __name__ == "__main__":
    test_over_fetch_requests_k_times_multiplier_from_inner()
    test_rerank_fn_changes_order_and_result_truncated_to_k()
    test_empty_inner_result_short_circuits_without_calling_rerank_fn()
    test_add_forwards_to_inner()
    test_invalid_over_fetch_rejected()
    test_flashrank_rerank_fn_raises_clear_error_without_dependency()
    print("OK: RerankedMemory 测试全部通过")
