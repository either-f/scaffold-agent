"""Planner 测试：JSON 解析失败重试一次再抛错（不再静默降级为最终答案），
以及原生 tool_calls 优先于文本 JSON 解析。

运行：PYTHONPATH=src python3 tests/test_planners.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.tools_local import default_toolbox
from agent_kernel.planners.react import ActionParseError, ReactPlanner
from agent_kernel.ports import ModelPort
from agent_kernel.types import ModelOutput, RunState, ToolCall


class SequenceModel(ModelPort):
    """按顺序返回预设的 ModelOutput；每次 complete() 记一次调用。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, messages, tools):
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


def test_parse_raises_on_garbage_text():
    with pytest.raises(ActionParseError):
        ReactPlanner._parse("这不是 JSON，纯胡说八道")


def test_parse_raises_when_missing_tool_and_final():
    with pytest.raises(ActionParseError):
        ReactPlanner._parse('{"thought": "只有 thought，没有 tool 也没有 final"}')


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


if __name__ == "__main__":
    test_parse_raises_on_garbage_text()
    test_parse_raises_when_missing_tool_and_final()
    test_step_retries_once_then_succeeds()
    test_step_raises_after_second_failure()
    test_native_tool_calls_bypass_text_parsing()
    print("OK: planner 测试全部通过")
