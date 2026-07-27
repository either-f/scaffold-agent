"""脚本化假模型：按预设顺序返回响应，用于离线测试内核与策略。

融合纪律第 3 条：每个真 adapter（LiteLLM 等）都必须有 Fake 对照实现。
"""
from __future__ import annotations

from ..ports import ModelPort
from ..types import Message, ModelOutput, ToolSpec


class FakeScriptedModel(ModelPort):
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        self.calls += 1
        if not self.script:
            return ModelOutput(text='{"thought": "脚本耗尽", "final": "（无更多脚本步骤）"}')
        return ModelOutput(text=self.script.pop(0), usage={"prompt_tokens": 0, "completion_tokens": 0})
