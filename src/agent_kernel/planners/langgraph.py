"""LangGraph 适配策略：注入已编译图，翻译其输出为内核标准 Action。

接受一个外部编译图对象（仅需 invoke(dict) -> mapping），将 RunState 与可用工具
序列化后喂入，严格翻译图输出为 ToolCall / FinalAnswer。不依赖 langgraph 包本身。
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from ..ports import MemoryPort, ModelPort, PlannerPort, ToolPort
from ..types import Action, FinalAnswer, Message, RunState, ToolCall


class CompiledGraph(Protocol):
    def invoke(self, input: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]: ...


class LangGraphPlanner(PlannerPort):
    """把外部 LangGraph 编译图适配为 PlannerPort。

    图输出契约：
      {"final": "非空答案"}                   -> FinalAnswer
      {"tool": "工具名", "args": {...}, "thought"?: "..."} -> ToolCall
    其它结构一律拒绝。
    """

    def __init__(self, graph: CompiledGraph) -> None:
        self.graph = graph

    def step(
        self,
        state: RunState,
        model: ModelPort,
        tools: ToolPort,
        memory: MemoryPort | None,
    ) -> Action:
        tool_specs = tools.list_tools()
        known_tools = {t.name for t in tool_specs}
        payload: dict[str, Any] = {
            "messages": [
                {"role": m.role, "content": m.content, "name": m.name}
                for m in state.messages
            ],
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in tool_specs
            ],
            "step": state.step,
            "context_summary": state.context_summary,
        }
        result = self.graph.invoke(payload)
        return self._translate(result, known_tools)

    @staticmethod
    def _translate(result: Any, known_tools: set[str]) -> Action:
        if not isinstance(result, dict):
            raise ValueError(f"图输出必须是 dict，得到 {type(result).__name__}")
        if "final" in result:
            final = result["final"]
            if not isinstance(final, str) or not final.strip():
                raise ValueError("final 必须是非空字符串")
            return FinalAnswer(content=str(final), thought=str(result.get("thought", "")))
        if "tool" in result:
            tool_name = result["tool"]
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError("tool 必须是非空字符串")
            if tool_name not in known_tools:
                raise ValueError(f"未知工具: {tool_name}")
            args = result.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("args 必须是 dict")
            return ToolCall(
                name=tool_name,
                args=args,
                thought=str(result.get("thought", "")),
            )
        raise ValueError(
            f"图输出必须包含 final 或 tool 字段，得到键: {list(result.keys())}"
        )
