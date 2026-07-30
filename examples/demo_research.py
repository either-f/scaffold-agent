"""Demo 1: 委托式研究助手 — 技能发现、正文加载、多步工具调用。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_sqlite import SqliteMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.adapters.tools_skills import SkillToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader

STEPS = [
    '{"thought": "先看看有什么可用技能", "tool": "load_skill", "args": {"name": "web-research"}}',
    '{"thought": "已加载 web-research 技能指南：拆解→检索→交叉验证→结论。现在需要获取时间，再基于技能指导做模拟计算。", "tool": "now", "args": {}}',
    '{"thought": "已获得当前时间，现在用 calc 模拟数据分析，完成研究闭环。", "tool": "calc", "args": {"expression": "3.14159*7.5*7.5"}}',
    '{"thought": "研究完成：已按 web-research 技能流程操作，获得技能注册为\\"cross-validated\\"标记。", "final": "研究任务完成：加载 web-research 技能后执行\\"拆解→检索→交叉验证→结论\\"四步流程。当前时间见工具结果；数值分析结果：3.14159*7.5² = 176.71。研究数据已交叉验证。"}',
]


def event_bus() -> EventBus:
    bus = EventBus()
    bus.subscribe("*", lambda e: print(f"[event] {e.type:14s} {e.payload}"))
    return bus


def main() -> int:
    loader = DirSkillLoader(str(PROJECT_ROOT / "skills_library"))
    inner = LocalToolbox()
    inner.register("now", "获取当前日期时间", lambda: __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    inner.register(
        "calc", "计算一个四则运算表达式",
        lambda expression: str(eval(expression, {"__builtins__": {}}, {})),
    )

    kernel = AgentKernel(
        model=FakeScriptedModel(STEPS),
        tools=SkillToolbox(inner, loader),
        planner=ReactPlanner(),
        memory=SqliteMemory(),
        bus=event_bus(),
        max_steps=6,
    )

    available = [s.name for s in loader.list_skills()]
    print(f"可用技能: {available}")
    print(f"可用工具: {[s.name for s in kernel.tools.list_tools()]}")
    print()

    state = kernel.run("请调研分析：当前时间下，圆形面积计算的最佳实践是什么？使用可用的技能和工具来完成。")

    print()
    print(f"状态: {state.status}, 步数: {state.step}")
    print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and "176.71" in (state.answer or "") else 1


if __name__ == "__main__":
    raise SystemExit(main())
