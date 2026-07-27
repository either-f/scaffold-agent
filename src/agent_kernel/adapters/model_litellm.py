"""LiteLLM 模型 adapter：一套接口调 100+ 模型（M1 联调）。

安装：uv sync --extra model   （或 pip install litellm）
密钥：走环境变量（如 ANTHROPIC_API_KEY / OPENAI_API_KEY），禁止硬编码。
"""
from __future__ import annotations

import json

from ..ports import ModelPort
from ..types import Message, ModelOutput, ToolSpec


class LiteLLMModel(ModelPort):
    def __init__(self, model: str, **kwargs) -> None:
        try:
            import litellm  # noqa: F401
        except ImportError as exc:  # 融合纪律：第三方依赖只在 adapter 内出现
            raise ImportError("需要 litellm：uv sync --extra model") from exc
        if not model:
            raise ValueError("model 不能为空")
        self.model = model
        self.kwargs = kwargs

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        import litellm

        resp = litellm.completion(
            model=self.model,
            messages=[{"role": m.role if m.role != "tool" else "user", "content": m.content} for m in messages],
            **self.kwargs,
        )
        choice = resp.choices[0].message
        content = choice.get("content") if isinstance(choice, dict) else choice.content
        if content is None:
            raise ValueError("模型返回空内容")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        usage = getattr(resp, "usage", None)
        prompt_tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else getattr(usage, "prompt_tokens", 0)
        completion_tokens = (
            usage.get("completion_tokens", 0)
            if isinstance(usage, dict)
            else getattr(usage, "completion_tokens", 0)
        )
        return ModelOutput(
            text=content,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )
