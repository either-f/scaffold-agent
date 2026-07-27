"""LangChain BaseTool 到 ToolPort 的可选兼容层。"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..ports import ToolPort
from ..types import ToolSpec


class LangChainToolbox(ToolPort):
    def __init__(self, tools: Iterable[Any]) -> None:
        try:
            from langchain_core.tools import BaseTool
        except ImportError as exc:
            raise ImportError("需要 langchain-core：uv sync --extra langchain") from exc
        tool_list = list(tools)
        if any(not isinstance(tool, BaseTool) for tool in tool_list):
            raise TypeError("tools 必须全部是 BaseTool")
        self._tools = {tool.name: tool for tool in tool_list}
        if len(self._tools) != len(tool_list):
            raise ValueError("LangChain 工具名不能重复")

    def list_tools(self) -> list[ToolSpec]:
        specs = []
        for tool in self._tools.values():
            schema = tool.args_schema
            if isinstance(schema, dict):
                parameters = schema
            elif schema is not None and hasattr(schema, "model_json_schema"):
                parameters = schema.model_json_schema()
            elif schema is not None and hasattr(schema, "schema"):
                parameters = schema.schema()
            else:
                parameters = {}
            specs.append(ToolSpec(tool.name, tool.description or "", parameters))
        return specs

    def call(self, name: str, args: dict) -> str:
        if name not in self._tools:
            raise KeyError(f"未知工具: {name}")
        result = self._tools[name].invoke(args)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
