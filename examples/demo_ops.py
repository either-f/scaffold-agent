r"""Demo 3: HITL 审批 + 沙箱安全策略验证 --- 离线命令策略干跑。

使用真实的 DockerSandbox 构建安全命令，通过注入的 validating runner 验证命令包含所有必需的
安全控制参数后再返回模拟输出。不启动容器、不修改文件、不落地 checkpoint。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory.sqlite import SqliteMemory
from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.sandbox_docker import DockerSandbox, SandboxToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.codeact import CodeActPlanner
from agent_kernel.types import ToolCall

STEPS = [
    json.dumps({"thought": "需要先计算长方形面积，写 Python 代码在沙箱中执行", "tool": "python_execute", "args": {"code": "w=12;h=8;a=w*h;print(f'面积: {w}*{h}={a}')"}}),
    json.dumps({"thought": "沙箱输出面积=96。接下来计算周长", "tool": "python_execute", "args": {"code": "w=12;h=8;p=2*(w+h);print(f'周长: {p}')"}}),
    json.dumps({"thought": "两个指标均已计算完成。沙箱安全策略验证通过（离线，未启动真实容器）。", "final": "安全策略干跑验证通过（离线，未启动真实容器，未写入 checkpoint）。长方形 12x8 面积=96，周长=40。Docker 命令已通过安全控制验证：--network none --read-only --cap-drop ALL --security-opt no-new-privileges。"}),
]

APPROVED = [True, True]
_approval_index = 0


def _validate_docker_cmd(cmd: list[str]) -> None:
    expected = DockerSandbox().build_command()
    if list(cmd) != expected:
        raise AssertionError(f"Docker 命令与安全基线不匹配")

def cli_approval(call: ToolCall) -> bool:
    global _approval_index
    approved = APPROVED[_approval_index] if _approval_index < len(APPROVED) else False
    _approval_index += 1
    print(f"\n[HITL] 待审批工具: {call.name}")
    print(f"[HITL] 参数: {json.dumps(call.args, ensure_ascii=False)}")
    print(f"[HITL] 结果: {'通过' if approved else '拒绝'}")
    return approved


def validating_runner_factory(stdouts: list[str]):
    _idx = 0

    def runner(cmd, **kwargs):
        nonlocal _idx
        _validate_docker_cmd(cmd)
        print("[安全策略] Docker 命令已通过安全控制验证（离线，未启动容器）")
        out = stdouts[_idx] if _idx < len(stdouts) else ""
        _idx += 1
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return runner


def main() -> int:
    global _approval_index
    _approval_index = 0

    sandbox = DockerSandbox(runner=validating_runner_factory(["面积: 12*8=96\n", "周长: 40\n"]))
    kernel = AgentKernel(
        model=FakeScriptedModel(list(STEPS)),
        tools=SandboxToolbox(sandbox),
        planner=CodeActPlanner(),
        memory=SqliteMemory(),
        bus=EventBus(),
        approval=cli_approval,
        max_steps=6,
    )

    print("运行模式: 离线 Docker 命令安全策略干跑（未启动真实容器，不写 checkpoint）")
    print(f"可用工具: {[s.name for s in kernel.tools.list_tools()]}")
    print(f"Planner: {type(kernel.planner).__name__}")
    print("HITL: 已启用（每个工具调用均需审批）")
    print()

    state = kernel.run("请在安全沙箱中计算长方形 12x8 的面积和周长，每步需经审批后执行。")

    print()
    print(f"状态: {state.status}, 步数: {state.step}")
    print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and _approval_index == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
