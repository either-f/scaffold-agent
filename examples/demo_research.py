r"""Demo 1: 委托式研究助手 --- 父 kernel 通过 WorkerDelegationPort 委派给研究 worker。

研究 worker 使用真正的 SkillToolbox + DirSkillLoader 加载 web-research 技能并调用本地工具。
父 kernel 只暴露 `worker_research` 工具，通过 WorkerDelegationPort 委托执行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_sqlite import SqliteMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_agents import WorkerDelegationPort
from agent_kernel.adapters.tools_local import LocalToolbox, safe_calc
from agent_kernel.adapters.tools_skills import SkillToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader
from agent_kernel.types import RunState

PARENT_STEPS = [
    json.dumps({"thought": "委托研究 worker 分析圆形面积计算", "tool": "worker_research", "args": {"task": "调研圆形面积计算的最佳实践"}}),
    json.dumps({"thought": "worker 返回了研究结果", "final": "委派任务完成：worker 报告圆形面积 pi*r^2，r=7.5~176.71"}),
]

WORKER_STEPS = [
    json.dumps({"thought": "先加载 web-research 技能了解研究方法", "tool": "load_skill", "args": {"name": "web-research"}}),
    json.dumps({"thought": "按技能规范拆解问题，计算圆形面积", "tool": "calc", "args": {"expression": "3.14159*7.5*7.5"}}),
    json.dumps({"thought": "研究完成：拆解→计算→结论", "final": "研究结果：圆形面积=pi*r^2，r=7.5 时面积≈176.71"}),
]


def main() -> int:
    loader = DirSkillLoader(str(PROJECT_ROOT / "skills_library"))
    worker_inner = LocalToolbox()
    worker_inner.register("calc", "计算四则运算表达式", lambda expression: safe_calc(expression))
    worker_tools = SkillToolbox(worker_inner, loader)

    worker = AgentKernel(
        model=FakeScriptedModel(list(WORKER_STEPS)),
        tools=worker_tools,
        planner=ReactPlanner(),
        memory=SqliteMemory(),
        bus=EventBus(),
        max_steps=6,
    )

    parent_inner = LocalToolbox()
    delegation = WorkerDelegationPort(parent_inner)
    delegation.register("research", worker, "将研究任务委派给 research worker")

    parent = AgentKernel(
        model=FakeScriptedModel(list(PARENT_STEPS)),
        tools=delegation,
        planner=ReactPlanner(),
        memory=SqliteMemory(),
        bus=EventBus(),
        max_steps=4,
    )

    available = [s.name for s in loader.list_skills()]
    print(f"可用技能: {available}")
    print(f"父 kernel 工具: {[s.name for s in parent.tools.list_tools()]}")
    print()

    state = parent.run("请委托研究 worker 调研分析圆形面积计算", state=RunState(run_id="demo-research-0001"))

    print()
    print(f"状态: {state.status}, 步数: {state.step}")
    print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and "176.71" in (state.answer or "") else 1


if __name__ == "__main__":
    raise SystemExit(main())
