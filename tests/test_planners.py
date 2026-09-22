"""Planner 测试：JSON 解析失败重试一次再抛错（不再静默降级为最终答案），
以及原生 tool_calls 优先于文本 JSON 解析。

运行：PYTHONPATH=src python3 tests/test_planners.py   （也兼容 pytest）
"""
import json
import sys

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.tools.local import default_toolbox
from agent_kernel.planners.plan_execute import PlanExecutePlanner
from agent_kernel.planners.react import ActionParseError, ReactPlanner
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.types import ModelOutput, RunState, ToolCall, ToolResult, ToolSpec


class SequenceModel(ModelPort):
    """按顺序返回预设的 ModelOutput；每次 complete() 记一次调用。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, messages, tools):
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


class CapturingModel(ModelPort):
    """记录每次 complete() 收到的 prompt 和 tools，用于验证注入的工具集。"""

    def __init__(self, output: ModelOutput):
        self._output = output
        self.last_messages: list | None = None
        self.last_tools: list[ToolSpec] | None = None

    def complete(self, messages, tools):
        self.last_messages = messages
        self.last_tools = list(tools)
        return self._output


class FakeToolbox(ToolPort):
    """带 N 个工具的测试桩；list_tools 返回全部，search_tools 走默认实现。"""

    def __init__(self, count: int = 10):
        self._specs = [
            ToolSpec(
                name=f"tool_{i}",
                description=f"工具 {i} 的描述 keyword_{i}",
                parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            )
            for i in range(count)
        ]

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs)

    def call(self, name: str, args: dict) -> ToolResult:
        return ToolResult(content="ok")


def test_parse_raises_on_garbage_text():
    with pytest.raises(ActionParseError):
        ReactPlanner._parse("这不是 JSON，纯胡说八道")


def test_parse_raises_when_missing_tool_and_final():
    with pytest.raises(ActionParseError):
        ReactPlanner._parse('{"thought": "只有 thought，没有 tool 也没有 final"}')


def test_parse_rejects_empty_final():
    """final 存在但为空串，必须抛 ActionParseError（不得静默完成 run）。"""
    with pytest.raises(ActionParseError):
        ReactPlanner._parse('{"thought": "答案来了", "final": ""}')


def test_parse_rejects_whitespace_only_final():
    """final 存在但纯空白，必须抛 ActionParseError。"""
    with pytest.raises(ActionParseError):
        ReactPlanner._parse('{"thought": "答案来了", "final": "   "}')


def test_step_retries_once_then_succeeds():
    model = SequenceModel(
        [
            ModelOutput(text="不是合法 JSON"),
            ModelOutput(text='{"thought": "重试后好了", "final": "答案来了"}'),
        ]
    )
    planner = ReactPlanner()
    action = planner.step(RunState(), model, default_toolbox(), None)
    assert model.calls == 2  # 第一次失败，重试了一次
    assert action.content == "答案来了"


def test_step_raises_after_second_failure():
    model = SequenceModel([ModelOutput(text="第一次也不是 JSON"), ModelOutput(text="第二次还不是 JSON")])
    planner = ReactPlanner()
    with pytest.raises(ActionParseError):
        planner.step(RunState(), model, default_toolbox(), None)
    assert model.calls == 2  # 只重试一次，不会无限重试


def test_native_tool_calls_bypass_text_parsing():
    """ModelOutput.tool_calls 非空时直接采用，不走文本 JSON 解析（哪怕 text 是空/垃圾）。"""
    model = SequenceModel(
        [ModelOutput(text="", tool_calls=[{"name": "calc", "args": {"expression": "1+1"}}])]
    )
    planner = ReactPlanner()
    action = planner.step(RunState(), model, default_toolbox(), None)
    assert model.calls == 1  # 没有触发重试
    assert isinstance(action, ToolCall)
    assert action.name == "calc"
    assert action.args == {"expression": "1+1"}


def test_native_tool_call_id_propagates_to_action():
    """原生 tool_calls 带的 id 必须原样进入返回的 ToolCall.call_id。"""
    model = SequenceModel(
        [
            ModelOutput(
                text="",
                tool_calls=[{"name": "calc", "args": {"expression": "1+1"}, "id": "call_42"}],
            )
        ]
    )
    planner = ReactPlanner()
    action = planner.step(RunState(), model, default_toolbox(), None)
    assert isinstance(action, ToolCall)
    assert action.call_id == "call_42"


def test_plan_execute_native_tool_calls_records_placeholder_plan():
    """原生 tool_calls 且无文本 plan 时，RunState.messages 仍记录占位计划消息，不静默丢弃。"""
    model = SequenceModel(
        [ModelOutput(text="", tool_calls=[{"name": "calc", "args": {"expression": "1+1"}}])]
    )
    planner = PlanExecutePlanner()
    state = RunState()
    action = planner.step(state, model, default_toolbox(), None)
    assert isinstance(action, ToolCall)
    assert action.name == "calc"
    # 必须有一条 [计划] 消息，说明模型跳过了计划（而非静默无记录）
    plan_msgs = [m for m in state.messages if m.role == "assistant" and m.content.startswith("[计划]")]
    assert len(plan_msgs) == 1
    assert "未提供文本计划" in plan_msgs[0].content


def test_plan_execute_native_tool_calls_uses_text_plan_if_present():
    """原生 tool_calls 且模型同时提供了文本 plan 时，优先采用文本中的 plan。"""
    model = SequenceModel(
        [
            ModelOutput(
                text='{"plan": "1. 计算 1+1"}',
                tool_calls=[{"name": "calc", "args": {"expression": "1+1"}}],
            )
        ]
    )
    planner = PlanExecutePlanner()
    state = RunState()
    action = planner.step(state, model, default_toolbox(), None)
    assert isinstance(action, ToolCall)
    plan_msgs = [m for m in state.messages if m.role == "assistant" and m.content.startswith("[计划]")]
    assert len(plan_msgs) == 1
    assert "计算 1+1" in plan_msgs[0].content


def test_plan_execute_text_json_path_unchanged():
    """非原生（文本 JSON）路径行为不变：plan 从 JSON 中提取。"""
    model = SequenceModel(
        [ModelOutput(text='{"thought": "先计划", "plan": "1. 计算", "tool": "calc", "args": {"expression": "2+2"}}')]
    )
    planner = PlanExecutePlanner()
    state = RunState()
    action = planner.step(state, model, default_toolbox(), None)
    assert isinstance(action, ToolCall)
    assert action.name == "calc"
    plan_msgs = [m for m in state.messages if m.role == "assistant" and m.content.startswith("[计划]")]
    assert len(plan_msgs) == 1
    assert "计算" in plan_msgs[0].content


# ----------------------------------------------------------- tool_budget 测试


def test_no_tool_budget_injects_all_tools():
    """默认（tool_budget=None）注入全部工具--与改动前行为完全一致。"""
    tools = FakeToolbox(10)
    model = CapturingModel(ModelOutput(text='{"thought": "t", "final": "done"}'))
    planner = ReactPlanner()
    planner.step(RunState(), model, tools, None)
    assert model.last_tools is not None
    assert len(model.last_tools) == 10


def test_tool_budget_limits_injected_tools():
    """tool_budget=3 对 10 个工具的 toolbox 只注入最多 3 个工具描述。"""
    tools = FakeToolbox(10)
    model = CapturingModel(ModelOutput(text='{"thought": "t", "final": "done"}'))
    planner = ReactPlanner(tool_budget=3)
    planner.step(RunState(), model, tools, None)
    assert model.last_tools is not None
    assert len(model.last_tools) <= 3


def test_tool_budget_includes_last_used_tool():
    """budget 收窄后，最近成功调用过的工具即使不在检索结果里也会被补回。"""
    tools = FakeToolbox(10)
    # 先模拟一次已完成的工具调用（assistant 消息里带 tool 字段 + tool 结果消息）
    state = RunState()
    state.add("user", "做点什么")
    state.add("assistant", json.dumps({"thought": "t", "tool": "tool_8", "args": {}}))
    state.add("tool", "结果", name="tool_8")

    model = CapturingModel(ModelOutput(text='{"thought": "t", "final": "done"}'))
    planner = ReactPlanner(tool_budget=3)
    planner.step(state, model, tools, None)
    assert model.last_tools is not None
    # tool_8 是最近用过的，必须在注入集合里
    injected_names = {t.name for t in model.last_tools}
    assert "tool_8" in injected_names
    # 总数不超过 budget + 1（最近工具例外）
    assert len(model.last_tools) <= 4


def test_tool_budget_zero_still_injects_last_used():
    """budget=0 时检索结果为空，但最近用过的工具仍会被补回。"""
    tools = FakeToolbox(10)
    state = RunState()
    state.add("user", "做点什么")
    state.add("assistant", json.dumps({"thought": "t", "tool": "tool_5", "args": {}}))
    state.add("tool", "结果", name="tool_5")

    model = CapturingModel(ModelOutput(text='{"thought": "t", "final": "done"}'))
    planner = ReactPlanner(tool_budget=0)
    planner.step(state, model, tools, None)
    assert model.last_tools is not None
    injected_names = {t.name for t in model.last_tools}
    assert "tool_5" in injected_names


def test_tool_budget_no_last_used_exact_limit():
    """没有最近工具调用时，注入数量严格不超过 budget。"""
    tools = FakeToolbox(10)
    model = CapturingModel(ModelOutput(text='{"thought": "t", "final": "done"}'))
    planner = ReactPlanner(tool_budget=3)
    planner.step(RunState(), model, tools, None)
    assert model.last_tools is not None
    assert len(model.last_tools) == 3  # 默认 search_tools 返回前 k 个


if __name__ == "__main__":
    test_parse_raises_on_garbage_text()
    test_parse_raises_when_missing_tool_and_final()
    test_parse_rejects_empty_final()
    test_parse_rejects_whitespace_only_final()
    test_step_retries_once_then_succeeds()
    test_step_raises_after_second_failure()
    test_native_tool_calls_bypass_text_parsing()
    test_native_tool_call_id_propagates_to_action()
    test_plan_execute_native_tool_calls_records_placeholder_plan()
    test_plan_execute_native_tool_calls_uses_text_plan_if_present()
    test_plan_execute_text_json_path_unchanged()
    test_no_tool_budget_injects_all_tools()
    test_tool_budget_limits_injected_tools()
    test_tool_budget_includes_last_used_tool()
    test_tool_budget_zero_still_injects_last_used()
    test_tool_budget_no_last_used_exact_limit()
    print("OK: planner 测试全部通过")

