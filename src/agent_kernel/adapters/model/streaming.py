"""StreamingModelPort：把一个"逐块产出"的底层调用（token_iter_fn）包成标准
ModelPort——调用方（ReactPlanner/DagPlanner/kernel）拿到的还是 complete()
同步返回的完整 ModelOutput，全程无感知；文本 delta 边收边通过
streaming.emit_token() side-channel 广播出去，外部想要实时展示时用
streaming.stream_to(sink) 订阅。

token_iter_fn 每次可以 yield 一个纯文本片段（str，向后兼容旧用法，走
emit_token 广播），也可以 yield 一个 StreamChunk（文本和/或一个
ToolCallDelta）。原生流式 tool_calls 的参数是逐 token 吐出的 JSON 字符串
碎片，按 provider（OpenAI/litellm 统一格式）给的 index 分组累加，流结束后
每个 index 拼成一个完整的 {"name":..., "args": {...}}——不广播（结构化片段
不是给人看的 token，emit_token 只广播纯文本，模仿 react.py 的最终产物就是
ModelOutput.tool_calls，跟原生 complete() 路径同一个契约）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterable, Union

from ...ports import ModelPort
from ...types import Message, ModelOutput, ToolSpec
from ...streaming import emit_token


@dataclass
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""


@dataclass
class StreamChunk:
    text: str = ""
    tool_call_delta: ToolCallDelta | None = None


TokenOrChunk = Union[str, StreamChunk]
TokenIterFn = Callable[[list[Message], list[ToolSpec]], Iterable[TokenOrChunk]]


class StreamingModelPort(ModelPort):
    def __init__(self, token_iter_fn: TokenIterFn) -> None:
        self.token_iter_fn = token_iter_fn

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        text_parts: list[str] = []
        accum: dict[int, dict] = {}
        order: list[int] = []

        for item in self.token_iter_fn(messages, tools):
            chunk = item if isinstance(item, StreamChunk) else StreamChunk(text=item)
            if chunk.text:
                text_parts.append(chunk.text)
                emit_token(chunk.text)
            if chunk.tool_call_delta is not None:
                self._accumulate(chunk.tool_call_delta, accum, order)

        tool_calls = [self._finalize(accum[idx]) for idx in order]
        return ModelOutput("".join(text_parts), tool_calls=tool_calls)

    @staticmethod
    def _accumulate(delta: ToolCallDelta, accum: dict[int, dict], order: list[int]) -> None:
        if delta.index not in accum:
            accum[delta.index] = {"id": delta.id, "name": delta.name, "arguments": ""}
            order.append(delta.index)
        else:
            if delta.id:
                accum[delta.index]["id"] = delta.id
            if delta.name:
                accum[delta.index]["name"] = delta.name
        accum[delta.index]["arguments"] += delta.arguments_fragment

    @staticmethod
    def _finalize(call: dict) -> dict:
        try:
            args = json.loads(call["arguments"]) if call["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        return {"name": call["name"], "args": args}
