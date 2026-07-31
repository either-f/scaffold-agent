"""LiteLLMModel 原生 tool calling 测试：验证 ToolSpec 被翻译成 litellm tools schema、
响应里的 tool_calls 被正确解析成 ModelOutput.tool_calls，全程用 monkeypatch 假响应，
不打真实网络请求。跳过：本机未装 litellm（uv sync --extra model）时。

运行：PYTHONPATH=src python3 -m pytest tests/test_model_litellm.py
"""
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, "src")

import pytest

litellm = pytest.importorskip("litellm")

from agent_kernel.adapters.model_litellm import LiteLLMModel
from agent_kernel.types import Message, ToolSpec


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    function: FakeFunction


@dataclass
class FakeMessage:
    content: Any = None
    tool_calls: list = field(default_factory=list)


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class FakeResponse:
    choices: list
    usage: FakeUsage = field(default_factory=FakeUsage)


def test_passes_tools_schema_and_parses_tool_calls(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse(
            choices=[
                FakeChoice(
                    message=FakeMessage(
                        content=None,
                        tool_calls=[FakeToolCall(FakeFunction(name="calc", arguments='{"expression": "1+1"}'))],
                    )
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})
    output = model.complete([Message("user", "算 1+1")], [spec])

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {"name": "calc", "description": "计算", "parameters": spec.parameters},
        }
    ]
    assert output.tool_calls == [{"name": "calc", "args": {"expression": "1+1"}}]
    assert output.text == ""  # 原生 tool_calls 场景下 content 可以是 None，落到空串


def test_no_tools_means_no_tools_kwarg(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content="纯文本回答"))])

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = LiteLLMModel("fake/model")
    output = model.complete([Message("user", "你好")], [])

    assert "tools" not in captured
    assert output.text == "纯文本回答"
    assert output.tool_calls == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
