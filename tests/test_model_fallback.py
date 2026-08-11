"""FallbackModelPort 单测：超时降级、异常降级、全失败抛错、on_fallback 回调。

运行：PYTHONPATH=src python3 tests/test_model_fallback.py   （也兼容 pytest）
"""
import sys
import time

sys.path.insert(0, "src")

from agent_kernel.adapters.model.fallback import AllModelsFailedError, FallbackModelPort
from agent_kernel.ports import ModelPort
from agent_kernel.types import ModelOutput


class FixedModel(ModelPort):
    def __init__(self, text: str = "", raises: Exception | None = None, delay: float = 0.0) -> None:
        self.text = text
        self.raises = raises
        self.delay = delay
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return ModelOutput(self.text)


def test_primary_success_never_touches_fallback():
    primary = FixedModel("primary answer")
    fallback = FixedModel("fallback answer")
    model = FallbackModelPort(primary, [fallback])
    out = model.complete([], [])
    assert out.text == "primary answer"
    assert fallback.calls == 0


def test_primary_exception_falls_back():
    primary = FixedModel(raises=RuntimeError("boom"))
    fallback = FixedModel("fallback answer")
    model = FallbackModelPort(primary, [fallback])
    out = model.complete([], [])
    assert out.text == "fallback answer"


def test_primary_timeout_falls_back():
    primary = FixedModel("too slow", delay=0.3)
    fallback = FixedModel("fast fallback")
    model = FallbackModelPort(primary, [fallback], timeout=0.05)
    out = model.complete([], [])
    assert out.text == "fast fallback"


def test_all_fail_raises_with_all_errors_collected():
    primary = FixedModel(raises=ValueError("p"))
    fallback = FixedModel(raises=ValueError("f"))
    model = FallbackModelPort(primary, [fallback])
    try:
        model.complete([], [])
        assert False, "应该抛出 AllModelsFailedError"
    except AllModelsFailedError as exc:
        assert len(exc.errors) == 2


def test_on_fallback_callback_fires_with_index_and_exception():
    primary = FixedModel(raises=RuntimeError("boom"))
    fallback = FixedModel("ok")
    events = []
    model = FallbackModelPort(
        primary, [fallback], on_fallback=lambda i, m, e: events.append((i, str(e)))
    )
    model.complete([], [])
    assert events == [(0, "boom")]


def test_no_fallbacks_configured_still_raises_on_primary_failure():
    primary = FixedModel(raises=RuntimeError("boom"))
    model = FallbackModelPort(primary)
    try:
        model.complete([], [])
        assert False, "应该抛出 AllModelsFailedError"
    except AllModelsFailedError as exc:
        assert len(exc.errors) == 1


if __name__ == "__main__":
    test_primary_success_never_touches_fallback()
    test_primary_exception_falls_back()
    test_primary_timeout_falls_back()
    test_all_fail_raises_with_all_errors_collected()
    test_on_fallback_callback_fires_with_index_and_exception()
    test_no_fallbacks_configured_still_raises_on_primary_failure()
    print("OK: FallbackModelPort 测试全部通过")
