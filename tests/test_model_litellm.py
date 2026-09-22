"""LiteLLMModel 原生 tool calling 测试：验证 ToolSpec 被翻译成 litellm tools schema、
响应里的 tool_calls 被正确解析成 ModelOutput.tool_calls，全程用 monkeypatch 假响应，
不打真实网络请求。跳过：本机未装 litellm（uv sync --extra model）时。

运行：PYTHONPATH=src python3 -m pytest tests/test_model_litellm.py
"""
import sys
import json
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, "src")

import pytest

litellm = pytest.importorskip("litellm")

from agent_kernel.adapters.model.litellm import LiteLLMModel, NativeToolCallArgumentError
from agent_kernel.types import Message, ToolSpec


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    function: FakeFunction
    id: str | None = None


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
    assert output.tool_calls == [{"name": "calc", "args": {"expression": "1+1"}, "id": None}]
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


def test_malformed_arguments_raises(monkeypatch):
    """畸形 arguments JSON 必须抛 NativeToolCallArgumentError，不再静默降级为 {}。"""

    def fake_completion(**kwargs):
        return FakeResponse(
            choices=[
                FakeChoice(
                    message=FakeMessage(
                        content=None,
                        tool_calls=[
                            FakeToolCall(
                                FakeFunction(name="calc", arguments="not-json{"),
                                id="call_1",
                            )
                        ],
                    )
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})
    with pytest.raises(NativeToolCallArgumentError) as exc:
        model.complete([Message("user", "算 1+1")], [spec])
    assert exc.value.tool_name == "calc"
    assert "not-json{" in exc.value.raw_args


def test_non_dict_arguments_raises(monkeypatch):
    """解析成合法 JSON 但非 dict（如数组/字符串）也要抛错。"""

    def fake_completion(**kwargs):
        return FakeResponse(
            choices=[
                FakeChoice(
                    message=FakeMessage(
                        content=None,
                        tool_calls=[
                            FakeToolCall(
                                FakeFunction(name="calc", arguments='[1,2,3]'),
                                id="call_2",
                            )
                        ],
                    )
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})
    with pytest.raises(NativeToolCallArgumentError):
        model.complete([Message("user", "算 1+1")], [spec])


def test_empty_arguments_is_legal_zero_arg(monkeypatch):
    """空 arguments 仍视为合法零参调用 {}（不抛错）。"""

    def fake_completion(**kwargs):
        return FakeResponse(
            choices=[
                FakeChoice(
                    message=FakeMessage(
                        content=None,
                        tool_calls=[
                            FakeToolCall(FakeFunction(name="ping", arguments=""), id="call_3")
                        ],
                    )
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="ping", description="ping", parameters={"type": "object", "properties": {}})
    output = model.complete([Message("user", "ping")], [spec])
    assert output.tool_calls == [{"name": "ping", "args": {}, "id": "call_3"}]


def test_tool_call_id_round_trips(monkeypatch):
    """provider 返回的 tool_call id 必须原样进入返回的 ToolCall 字典。"""

    def fake_completion(**kwargs):
        return FakeResponse(
            choices=[
                FakeChoice(
                    message=FakeMessage(
                        content=None,
                        tool_calls=[
                            FakeToolCall(
                                FakeFunction(name="calc", arguments='{"expression": "1+1"}'),
                                id="call_abc",
                            )
                        ],
                    )
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})
    output = model.complete([Message("user", "算 1+1")], [spec])
    assert output.tool_calls == [
        {"name": "calc", "args": {"expression": "1+1"}, "id": "call_abc"}
    ]


def test_two_turn_native_tool_calling_sends_provider_shapes(monkeypatch):
    """两轮 native tool calling：第二轮发给 litellm 的 messages 必须包含
    assistant.tool_calls + role:tool/tool_call_id，而不是全部被压扁成 role:user。"""
    captured_prompts: list[dict] = []

    def fake_completion(**kwargs):
        captured_prompts.append(kwargs)
        # 第一次：模型发起 tool call
        if len(captured_prompts) == 1:
            return FakeResponse(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=None,
                            tool_calls=[
                                FakeToolCall(
                                    FakeFunction(name="calc", arguments='{"expression": "1+1"}'),
                                    id="call_xyz",
                                )
                            ],
                        )
                    )
                ]
            )
        # 第二次：模型给最终答案
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content="结果是 2"))])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    spec = ToolSpec(name="calc", description="计算", parameters={"type": "object", "properties": {}})

    # 第一轮：模型发起 native tool call
    out1 = model.complete([Message("user", "算 1+1")], [spec])
    assert out1.tool_calls[0]["id"] == "call_xyz"

    # 模拟内核 _finish_tool 在 native 路径下记录的 messages（带 tool_calls / tool_call_id）
    history = [
        Message("user", "算 1+1"),
        Message(
            "assistant",
            "",
            tool_calls=[{"id": "call_xyz", "name": "calc", "args": {"expression": "1+1"}}],
        ),
        Message("tool", "2", name="calc", tool_call_id="call_xyz"),
    ]

    # 第二轮：把历史喂回去，验证 wire shape
    model.complete(history, [spec])

    second_msgs = captured_prompts[1]["messages"]
    # assistant 消息还原成 tool_calls 形状
    asst = next(m for m in second_msgs if m["role"] == "assistant")
    assert "tool_calls" in asst
    assert asst["tool_calls"][0]["id"] == "call_xyz"
    assert asst["tool_calls"][0]["function"]["name"] == "calc"
    assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"expression": "1+1"}
    # tool 消息还原成 role:tool + tool_call_id（不是 role:user）
    tool_msgs = [m for m in second_msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_xyz"
    assert tool_msgs[0]["content"] == "2"
    # 不能有任何 tool 消息被降级成 user
    assert not any(m["role"] == "user" and m["content"] == "2" for m in second_msgs)


def test_non_native_tool_message_still_flattened_to_user(monkeypatch):
    """非 native 路径（Message 无 tool_call_id）：tool 角色仍映射成 user，字节不变。"""
    captured_prompts: list[dict] = []

    def fake_completion(**kwargs):
        captured_prompts.append(kwargs)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content="ok"))])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    model = LiteLLMModel("fake/model")
    history = [
        Message("user", "算 1+1"),
        Message("assistant", '{"thought":"t","tool":"calc","args":{"expression":"1+1"}}'),
        Message("tool", "2", name="calc"),  # 无 tool_call_id -> 非 native
    ]
    model.complete(history, [])
    msgs = captured_prompts[0]["messages"]
    # tool 消息被压扁成 user
    assert any(m["role"] == "user" and m["content"] == "2" for m in msgs)
    # 没有 role:tool 出现
    assert not any(m["role"] == "tool" for m in msgs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
