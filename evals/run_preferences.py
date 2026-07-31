"""偏好记忆注入 eval：验证 ReactPlanner 每轮无条件把 preferences 命中塞进 system prompt，
不像常规记忆那样依赖跟当前用户输入的相关性；没配置 preferences 时不产生多余的块。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import MemoryPort, ModelPort
from agent_kernel.types import ModelOutput, ToolSpec


class FixedMemory(MemoryPort):
    def __init__(self, hits: list[str]) -> None:
        self.hits = hits

    def add(self, run_id: str, role: str, content: str) -> None:
        pass

    def search(self, query: str, k: int = 5) -> list[str]:
        return self.hits[:k]


class CapturingModel(ModelPort):
    def __init__(self) -> None:
        self.last_system = ""

    def complete(self, messages, tools: list[ToolSpec]) -> ModelOutput:
        self.last_system = messages[0].content
        return ModelOutput('{"thought": "done", "final": "ok"}')


def run_preferences() -> dict:
    preferences = FixedMemory(["用户偏好使用中文回复。", "禁止使用表情符号。"])
    model = CapturingModel()
    kernel = AgentKernel(
        model=model,
        tools=LocalToolbox(),
        planner=ReactPlanner(preferences=preferences),
        max_steps=2,
    )
    # 提问跟偏好毫无语义关联，验证偏好块不看当前 query 相关性，每轮都注入
    kernel.run("今天天气怎么样？")
    injected = (
        "用户偏好使用中文回复" in model.last_system
        and "禁止使用表情符号" in model.last_system
    )

    empty_model = CapturingModel()
    empty_kernel = AgentKernel(
        model=empty_model,
        tools=LocalToolbox(),
        planner=ReactPlanner(),
        max_steps=2,
    )
    empty_kernel.run("今天天气怎么样？")
    no_block_without_preferences = "已知偏好与约束" not in empty_model.last_system

    ok = injected and no_block_without_preferences
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "preferences",
        "injected_regardless_of_query": injected,
        "no_block_without_preferences": no_block_without_preferences,
        "ok": ok,
    }


def main() -> int:
    report = run_preferences()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
