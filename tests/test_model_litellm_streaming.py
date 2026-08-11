"""litellm_streaming_model 集成测试：验证 stream=True 请求参数组装、逐 chunk
的 delta.content 被正确拼成完整文本、跟 streaming.stream_to 旁路正确联动，以及
delta.tool_calls 碎片按 index 正确拼成完整 tool_calls（含 tools 参数确实被
传给 litellm——这是流式路径此前遗漏的，不传 tools 模型永远不会触发 tool_calls）。
全程 monkeypatch 假的流式响应（一个 chunk 对象列表），不打真实网络请求。
跳过：本机未装 litellm（uv sync --extra model）时。

运行：PYTHONPATH=src python3 -m pytest tests/test_model_litellm_streaming.py
"""
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, "src")

import pytest

litellm = pytest.importorskip("litellm")

from agent_kernel.adapters.model.litellm import litellm_streaming_model
from agent_kernel.streaming import stream_to
from agent_kernel.types import Message, ToolSpec


@dataclass
class FakeFunctionDelta:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCallDelta:
    index: int
    id: str | None = None
    function: FakeFunctionDelta = field(default_factory=FakeFunctionDelta)


@dataclass
class FakeDelta:
    content: Any = None
    tool_calls: list = field(default_factory=list)


@dataclass
class FakeStreamChoice:
    delta: FakeDelta


@dataclass
class FakeChunk:
    choices: list


def _chunks(*contents: str | None) -> list:
    return [FakeChunk(choices=[FakeStreamChoice(FakeDelta(c))]) for c in contents]


def _tool_call_chunks(*deltas: FakeToolCallDelta) -> list:
    return [FakeChunk(choices=[FakeStreamChoice(FakeDelta(tool_calls=[d]))]) for d in deltas]


def test_streams_and_aggregates_delta_content(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _chunks("Hel", "lo", ", ", "world", "!")

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model")
    output = model.complete([Message("user", "hi")], [])

    assert captured["stream"] is True
    assert captured["model"] == "fake/model"
    assert output.text == "Hello, world!"
    assert output.tool_calls == []


def test_empty_delta_chunks_are_skipped(monkeypatch):
    def fake_completion(**kwargs):
        return _chunks("a", None, "", "b")  # None/空串不该拼进结果

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model")
    output = model.complete([], [])
    assert output.text == "ab"


def test_broadcasts_each_chunk_via_stream_to(monkeypatch):
    def fake_completion(**kwargs):
        return _chunks("x", "y", "z")

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model")
    captured = []
    with stream_to(captured.append):
        output = model.complete([], [])

    assert captured == ["x", "y", "z"]
    assert output.text == "xyz"


def test_extra_kwargs_forwarded_to_completion(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _chunks("ok")

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model", temperature=0, timeout=30)
    model.complete([], [])
    assert captured["temperature"] == 0
    assert captured["timeout"] == 30


def test_streams_and_assembles_fragmented_tool_call(monkeypatch):
    def fake_completion(**kwargs):
        return _tool_call_chunks(
            FakeToolCallDelta(index=0, id="call_1", function=FakeFunctionDelta(name="get_weather", arguments="")),
            FakeToolCallDelta(index=0, function=FakeFunctionDelta(arguments='{"cit')),
            FakeToolCallDelta(index=0, function=FakeFunctionDelta(arguments='y": "Beijing"}')),
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model")
    output = model.complete([], [])

    assert output.text == ""
    assert output.tool_calls == [{"name": "get_weather", "args": {"city": "Beijing"}}]


def test_tools_param_forwarded_as_litellm_tools_schema(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _chunks("ok")

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = litellm_streaming_model("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})
    model.complete([Message("user", "算 1+1")], [spec])

    assert captured["tools"] == [
        {"type": "function", "function": {"name": "calc", "description": "计算", "parameters": spec.parameters}}
    ]


def test_no_tools_means_no_tools_kwarg_in_streaming_path(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _chunks("ok")

    monkeypatch.setattr(litellm, "completion", fake_completion)

    litellm_streaming_model("fake/model").complete([], [])
    assert "tools" not in captured


def test_empty_model_name_rejected():
    try:
        litellm_streaming_model("")
        assert False, "空 model 应该拒绝"
    except ValueError:
        pass


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

