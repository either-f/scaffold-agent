"""IntentGatedMemory 单测：闲聊跳过检索、正常 query 照常转发、add 永远直通。

运行：PYTHONPATH=src python3 tests/test_intent_gate.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.memory.intent_gate import IntentGatedMemory
from agent_kernel.ports import MemoryPort, ModelPort
from agent_kernel.types import ModelOutput


class SpyMemory(MemoryPort):
    def __init__(self, tag: str = "") -> None:
        self.tag = tag
        self.added: list[tuple[str, str, str]] = []
        self.search_calls: list[tuple[str, int]] = []

    def add(self, run_id: str, role: str, content: str) -> None:
        self.added.append((run_id, role, content))

    def search(self, query: str, k: int = 5) -> list[str]:
        self.search_calls.append((query, k))
        return [f"hit{self.tag}-for-{query}"]


class ScriptedClassifier(ModelPort):
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return ModelOutput(self.verdict)


def test_casual_query_skips_inner_search():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner)
    assert gated.search("你好") == []
    assert inner.search_calls == []


def test_casual_query_case_insensitive_and_punctuation_tolerant():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner)
    assert gated.search("Hello!") == []
    assert gated.search("  谢谢~  ") == []
    assert inner.search_calls == []


def test_non_casual_query_forwards_to_inner():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner)
    result = gated.search("我们上次聊到的项目代号是什么？")
    assert result == ["hit-for-我们上次聊到的项目代号是什么？"]
    assert inner.search_calls == [("我们上次聊到的项目代号是什么？", 5)]


def test_add_always_forwards_regardless_of_content():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner)
    gated.add("r1", "user", "你好")
    assert inner.added == [("r1", "user", "你好")]


def test_custom_patterns_override_default():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner, casual_patterns=[r"^闲聊$"])
    assert gated.search("你好") == ["hit-for-你好"]  # 默认闲聊词不再生效
    assert gated.search("闲聊") == []


def test_dict_inner_requires_default_key():
    try:
        IntentGatedMemory({"identity": SpyMemory()})
        assert False, "缺 default 键应该拒绝"
    except ValueError:
        pass


def test_scope_pattern_routes_to_matching_memory():
    identity = SpyMemory(tag="-identity")
    default = SpyMemory(tag="-default")
    gated = IntentGatedMemory(
        {"identity": identity, "default": default},
        scope_patterns={"identity": [r"你是谁", r"你叫什么"]},
    )
    result = gated.search("你是谁")
    assert result == ["hit-identity-for-你是谁"]
    assert identity.search_calls == [("你是谁", 5)]
    assert default.search_calls == []


def test_unmatched_scope_falls_back_to_default_memory():
    identity = SpyMemory(tag="-identity")
    default = SpyMemory(tag="-default")
    gated = IntentGatedMemory(
        {"identity": identity, "default": default},
        scope_patterns={"identity": [r"你是谁"]},
    )
    result = gated.search("上次说的截止日期是哪天？")
    assert result == ["hit-default-for-上次说的截止日期是哪天？"]
    assert identity.search_calls == []


def test_scope_k_overrides_default_k():
    default = SpyMemory()
    boundary = SpyMemory(tag="-boundary")
    gated = IntentGatedMemory(
        {"boundary": boundary, "default": default},
        scope_patterns={"boundary": [r"不能|禁止"]},
        scope_k={"boundary": 2},
    )
    gated.search("你不能说这个", k=5)
    assert boundary.search_calls == [("你不能说这个", 2)]


def test_add_only_writes_default_scope_when_inner_is_dict():
    identity = SpyMemory()
    default = SpyMemory()
    gated = IntentGatedMemory({"identity": identity, "default": default})
    gated.add("r1", "user", "hello")
    assert default.added == [("r1", "user", "hello")]
    assert identity.added == []


def test_classifier_not_called_when_scope_regex_already_matched():
    default = SpyMemory()
    identity = SpyMemory(tag="-identity")
    classifier = ScriptedClassifier("skip")  # 如果被调用会导致误判 skip，用来验证没被调用
    gated = IntentGatedMemory(
        {"identity": identity, "default": default},
        scope_patterns={"identity": [r"你是谁"]},
        classifier_model=classifier,
    )
    result = gated.search("你是谁")
    assert result == ["hit-identity-for-你是谁"]
    assert classifier.calls == 0


def test_classifier_skip_verdict_suppresses_retrieval_when_regex_ambiguous():
    inner = SpyMemory()
    classifier = ScriptedClassifier("skip")
    gated = IntentGatedMemory(inner, classifier_model=classifier)
    assert gated.search("今天天气不错") == []
    assert classifier.calls == 1
    assert inner.search_calls == []


def test_classifier_retrieve_verdict_forwards_to_inner():
    inner = SpyMemory()
    classifier = ScriptedClassifier("retrieve")
    gated = IntentGatedMemory(inner, classifier_model=classifier)
    result = gated.search("今天天气不错")
    assert result == ["hit-for-今天天气不错"]
    assert classifier.calls == 1


def test_without_classifier_ambiguous_query_defaults_to_retrieve():
    inner = SpyMemory()
    gated = IntentGatedMemory(inner)  # 没配 classifier_model，退化成 v1：未命中正则＝照常检索
    result = gated.search("今天天气不错")
    assert result == ["hit-for-今天天气不错"]


if __name__ == "__main__":
    test_casual_query_skips_inner_search()
    test_casual_query_case_insensitive_and_punctuation_tolerant()
    test_non_casual_query_forwards_to_inner()
    test_add_always_forwards_regardless_of_content()
    test_custom_patterns_override_default()
    test_dict_inner_requires_default_key()
    test_scope_pattern_routes_to_matching_memory()
    test_unmatched_scope_falls_back_to_default_memory()
    test_scope_k_overrides_default_k()
    test_add_only_writes_default_scope_when_inner_is_dict()
    test_classifier_not_called_when_scope_regex_already_matched()
    test_classifier_skip_verdict_suppresses_retrieval_when_regex_ambiguous()
    test_classifier_retrieve_verdict_forwards_to_inner()
    test_without_classifier_ambiguous_query_defaults_to_retrieve()
    print("OK: IntentGatedMemory 测试全部通过")
