"""LiteLLM 模型 adapter：一套接口调 100+ 模型（M1 联调）。

安装：uv sync --extra model   （或 pip install litellm）
密钥：走环境变量（如 ANTHROPIC_API_KEY / OPENAI_API_KEY），禁止硬编码。
"""
from __future__ import annotations

import json

from ...ports import ModelPort
from ...types import Message, ModelOutput, ToolSpec
from .streaming import StreamChunk, StreamingModelPort, ToolCallDelta


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

        kwargs = dict(self.kwargs)
        if tools:
            kwargs["tools"] = [_to_litellm_tool(t) for t in tools]

        resp = litellm.completion(
            model=self.model,
            messages=[{"role": m.role if m.role != "tool" else "user", "content": m.content} for m in messages],
            **kwargs,
        )
        choice = resp.choices[0].message
        content = choice.get("content") if isinstance(choice, dict) else choice.content
        raw_tool_calls = choice.get("tool_calls") if isinstance(choice, dict) else choice.tool_calls
        tool_calls = [_parse_tool_call(tc) for tc in raw_tool_calls] if raw_tool_calls else []
        if content is None and not tool_calls:
            raise ValueError("模型既未返回内容也未返回 tool_calls")
        if content is None:
            content = ""
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
            tool_calls=tool_calls,
        )


def litellm_streaming_model(model: str, **kwargs) -> StreamingModelPort:
    """流式版 LiteLLMModel：litellm stream=True 逐 chunk 拉取，包成 StreamingModelPort。
    支持流式原生 tool_calls——OpenAI 兼容格式里 delta.tool_calls 按 index 分组，
    参数 JSON 是逐 chunk 吐出的字符串碎片，只在流结束后由 StreamingModelPort 统一
    拼接解析（见该模块），这里只负责把每个 provider chunk 转成 ToolCallDelta，
    原样透传碎片，不在这一层做任何拼接/解析。"""
    try:
        import litellm  # noqa: F401
    except ImportError as exc:  # 融合纪律：第三方依赖只在 adapter 内出现
        raise ImportError("需要 litellm：uv sync --extra model") from exc
    if not model:
        raise ValueError("model 不能为空")

    def token_iter_fn(messages: list[Message], tools: list[ToolSpec]):
        import litellm

        call_kwargs = dict(kwargs)
        if tools:
            call_kwargs["tools"] = [_to_litellm_tool(t) for t in tools]

        resp = litellm.completion(
            model=model,
            messages=[{"role": m.role if m.role != "tool" else "user", "content": m.content} for m in messages],
            stream=True,
            **call_kwargs,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta
            content = delta.get("content") if isinstance(delta, dict) else delta.content
            if content:
                yield content

            raw_tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else getattr(delta, "tool_calls", None)
            for tc in raw_tool_calls or []:
                yield StreamChunk(tool_call_delta=_parse_tool_call_delta(tc))

    return StreamingModelPort(token_iter_fn)


def _parse_tool_call_delta(tc) -> ToolCallDelta:
    index = tc.get("index") if isinstance(tc, dict) else getattr(tc, "index", None)
    call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
    function = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
    name = (function.get("name") if isinstance(function, dict) else getattr(function, "name", None)) if function else None
    arguments = (
        function.get("arguments") if isinstance(function, dict) else getattr(function, "arguments", None)
    ) if function else None
    return ToolCallDelta(index=index if index is not None else 0, id=call_id, name=name, arguments_fragment=arguments or "")


def _to_litellm_tool(spec: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters or {"type": "object", "properties": {}},
        },
    }


def _parse_tool_call(tc) -> dict:
    function = tc.get("function") if isinstance(tc, dict) else tc.function
    name = function.get("name") if isinstance(function, dict) else function.name
    raw_args = function.get("arguments") if isinstance(function, dict) else function.arguments
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}
    return {"name": name, "args": args}
