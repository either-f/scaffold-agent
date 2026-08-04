"""ToolResult/ArtifactRef 测试：OffloadingToolbox 落盘的产物必须以 ArtifactRef 暴露，
工具异常也要包装成 ToolResult 而不是裸字符串。

运行：PYTHONPATH=src python3 tests/test_tool_result.py   （也兼容 pytest）
"""
import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.tools.local import LocalToolbox
from agent_kernel.adapters.tools.offload import OffloadingToolbox
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import ToolResult


def test_local_toolbox_returns_tool_result():
    tools = LocalToolbox()
    tools.register("echo", "echo", lambda text: text)
    result = tools.call("echo", {"text": "hi"})
    assert isinstance(result, ToolResult)
    assert result.content == "hi"
    assert result.artifacts == []


def test_offloading_toolbox_attaches_artifact_for_long_result():
    raw = "X" * 5000
    with tempfile.TemporaryDirectory() as tmp:
        inner = LocalToolbox()
        inner.register("large", "large", lambda: raw)
        artifact_dir = Path(tmp) / "artifacts"
        offloaded = OffloadingToolbox(inner, artifact_dir=str(artifact_dir), max_inline_chars=100, preview_chars=10)
        result = offloaded.call("large", {})

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        expected_path = str((artifact_dir / f"{digest}.txt").resolve())

        assert len(result.artifacts) == 1
        assert result.artifacts[0].uri == expected_path
        assert expected_path in result.content
        assert Path(expected_path).read_text(encoding="utf-8") == raw


def test_offloading_toolbox_passes_through_short_result_untouched():
    inner = LocalToolbox()
    inner.register("short", "short", lambda: "ok")
    offloaded = OffloadingToolbox(inner, max_inline_chars=100)
    result = offloaded.call("short", {})
    assert result.content == "ok"
    assert result.artifacts == []


def test_tool_error_wrapped_as_tool_result_and_marked_failed():
    tools = LocalToolbox()

    def boom():
        raise RuntimeError("kaboom")

    tools.register("boom", "boom", boom)
    kernel = AgentKernel(
        model=FakeScriptedModel(
            [
                '{"thought": "t", "tool": "boom", "args": {}}',
                '{"thought": "t", "final": "已处理错误"}',
            ]
        ),
        tools=tools,
        planner=ReactPlanner(),
    )
    state = kernel.run("触发一个工具错误")
    assert state.status == "done"
    assert any("[tool-error]" in m.content and "kaboom" in m.content for m in state.messages if m.role == "tool")


if __name__ == "__main__":
    test_local_toolbox_returns_tool_result()
    test_offloading_toolbox_attaches_artifact_for_long_result()
    test_offloading_toolbox_passes_through_short_result_untouched()
    test_tool_error_wrapped_as_tool_result_and_marked_failed()
    print("OK: ToolResult 测试全部通过")
