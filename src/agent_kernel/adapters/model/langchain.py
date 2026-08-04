"""LangChain BaseChatModel 到 ModelPort 的可选兼容层。"""
from __future__ import annotations

import json
from typing import Any

from ...ports import ModelPort
from ...types import Message, ModelOutput, ToolSpec


class LangChainModel(ModelPort):
    def __init__(self, model: Any) -> None:
        try:
            from langchain_core.language_models import BaseChatModel
        except ImportError as exc:
            raise ImportError("需要 langchain-core：uv sync --extra langchain") from exc
        if not isinstance(model, BaseChatModel):
            raise TypeError("model 必须是 BaseChatModel")
        self.model = model

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        converted = []
        for message in messages:
            if message.role == "system":
                converted.append(SystemMessage(content=message.content))
            elif message.role == "assistant":
                converted.append(AIMessage(content=message.content))
            else:
                prefix = f"[tool {message.name}] " if message.role == "tool" else ""
                converted.append(HumanMessage(content=prefix + message.content))

        response = self.model.invoke(converted)
        content = response.content
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        usage = getattr(response, "usage_metadata", None) or {}
        return ModelOutput(
            text=text,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
        )
