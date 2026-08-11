"""FallbackModelPort：包一层超时 + 依次降级，任何 ModelPort 实现都能套。

跟 DagPlanner 的 harness（timeout + fallback 链）是同一套模式，只是这里包的是
model.complete() 本身而不是工具调用——FoxChat 的"gRPC 超时→降级 REST"就是这个
思路的跨语言版本。

ponytail: 只做整次调用级超时，不做 token 间隔超时/首 token 超时（那两个要感知
流式分片，ModelPort.complete() 目前是同步整段返回，没有这个信息）；等内核支持
流式输出时再补，FoxChat 的 StreamingTagParser FSM 模式到时候可以直接借。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Callable

from ...ports import ModelPort
from ...types import Message, ModelOutput, ToolSpec

OnFallback = Callable[[int, ModelPort, Exception], None]


class AllModelsFailedError(RuntimeError):
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        summary = "; ".join(f"{type(e).__name__}: {e}" for e in errors)
        super().__init__(f"全部 {len(errors)} 个模型均失败: {summary}")


class FallbackModelPort(ModelPort):
    def __init__(
        self,
        primary: ModelPort,
        fallbacks: list[ModelPort] | None = None,
        timeout: float | None = None,
        on_fallback: OnFallback | None = None,
    ) -> None:
        self.chain = [primary, *(fallbacks or [])]
        self.timeout = timeout
        self.on_fallback = on_fallback

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        errors: list[Exception] = []
        for i, model in enumerate(self.chain):
            try:
                return self._call(model, messages, tools)
            except Exception as exc:  # 超时或模型调用本身抛错，都算这一路失败，试下一路
                errors.append(exc)
                if self.on_fallback:
                    self.on_fallback(i, model, exc)
        raise AllModelsFailedError(errors)

    def _call(self, model: ModelPort, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        if self.timeout is None:
            return model.complete(messages, tools)
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(model.complete, messages, tools)
            try:
                return future.result(timeout=self.timeout)
            except FutureTimeoutError as exc:
                raise TimeoutError(f"模型调用超时({self.timeout}s)") from exc
