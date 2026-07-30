"""Demo 3: HITL 审批 + 沙箱运算 — 逐工具 CLI 审批 + CodeAct Docker 沙箱执行。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_sqlite import SqliteMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.sandbox_docker import DockerSandbox, SandboxToolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.codeact import CodeActPlanner
from agent_kernel.types import ToolCall

STEPS = [
    '{"thought": "需要先计算长方形面积，写 Python 代码在沙箱中执行", "tool": "python_execute", "args": {"code": "w=12;h=8;a=w*h;print(f\'面积: {w}*{h}={a}\')"}}',
    '{"thought": "沙箱输出面积=96。接下来计算周长", "tool": "python_execute", "args": {"code": "w=12;h=8;p=2*(w+h);print(f\'周长: {p}\')"}}',
    '{"thought": "两个指标均已计算完成：面积96、周长40。现在给出最终答案。", "final": "沙箱运算完成（逐工具经人工审批）：长方形 12×8 的面积=96，周长=40。沙箱隔离确认：无网络、无文件系统、只读根目录。"}',
]

APPROVED = [True, True]
_approval_index = 0


def event_bus() -> EventBus:
    bus = EventBus()
    bus.subscribe("*", lambda e: print(f"[event] {e.type:14s} {json.dumps(e.payload, ensure_ascii=False)}"))
    return bus


def cli_approval(call: ToolCall) -> bool:
    global _approval_index
    approved = APPROVED[_approval_index] if _approval_index < len(APPROVED) else False
    _approval_index += 1
    print(f"\n[HITL] 待审批工具: {call.tool if hasattr(call, 'tool') else call.name}")
    print(f"[HITL] 参数: {json.dumps(call.args, ensure_ascii=False)}")
    print(f"[HITL] 结果: {'通过' if approved else '拒绝'}")
    return approved


def fake_runner_factory(stdouts: list):
    _idx = 0

    def runner(cmd, **kwargs):
        nonlocal _idx
        out = stdouts[_idx] if _idx < len(stdouts) else ""
        _idx += 1
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return runner


def main() -> int:
    global _approval_index
    _approval_index = 0

    sandbox = DockerSandbox(runner=fake_runner_factory(["面积: 12*8=96\n", "周长: 40\n"]))
    kernel = AgentKernel(
        model=FakeScriptedModel(STEPS),
        tools=SandboxToolbox(sandbox),
        planner=CodeActPlanner(),
        memory=SqliteMemory(),
        bus=event_bus(),
        checkpoints=JsonCheckpointStore(str(PROJECT_ROOT / "runs")),
        approval=cli_approval,
        max_steps=6,
    )

    print("可用工具:", [s.name for s in kernel.tools.list_tools()])
    print("Planner:", type(kernel.planner).__name__)
    print("HITL: 已启用（每个工具调用均需审批）")
    print()

    state = kernel.run("请在安全沙箱中计算长方形 12×8 的面积和周长，每步需经审批后执行。")

    print()
    print(f"状态: {state.status}, 步数: {state.step}")
    print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and _approval_index == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
