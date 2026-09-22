"""agent-kernel 公共 API。

消费者只需 `from agent_kernel import AgentKernel, RunState, ...` 即可嵌入内核，
无需知道内部模块布局。适配器（带可选第三方依赖）仍按需从各自模块 import-on-demand，
不在此处 re-export，以免把 litellm/mcp/langchain 等变成硬依赖。

`__version__` 优先取已安装的包元数据；editable/uninstalled 开发检出里
importlib.metadata 查不到时，回退到 pyproject.toml 的版本字符串。
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from .kernel import AgentKernel
from .ports import MemoryPort, ModelPort, PlannerPort, ToolPort
from .types import (
    Event,
    FinalAnswer,
    Message,
    RunState,
    ToolCall,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "AgentKernel",
    "RunState",
    "Message",
    "ToolCall",
    "FinalAnswer",
    "ToolResult",
    "ToolSpec",
    "Event",
    "ModelPort",
    "ToolPort",
    "MemoryPort",
    "PlannerPort",
    "__version__",
]


def _resolve_version() -> str:
    try:
        return _pkg_version("agent-kernel")
    except PackageNotFoundError:
        # editable/未安装的 dev checkout：回退到 pyproject.toml 的版本
        try:
            from pathlib import Path
            import tomllib

            pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
            if pyproject.exists():
                with pyproject.open("rb") as f:
                    return tomllib.load(f).get("project", {}).get("version", "0.0.0")
        except Exception:
            pass
        return "0.0.0"


__version__ = _resolve_version()
