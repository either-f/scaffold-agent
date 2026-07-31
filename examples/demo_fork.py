r"""Demo 4: Fork 分支执行 --- 同一个 fork 点，批准/拒绝两条分支的真实结果对比。

跟 demo_ops.py 不同，这个 demo 必须落真实 checkpoint 才能演示"从历史快照 fork"，
所以用临时目录、不接入 record_demos.py 的确定性 .cast 录制系统（临时路径每次
不同，没法做字节级 diff）。跑完直接看输出，跟 run_demo.py 一样。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.fork import fork
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.types import ModelOutput, RunState, ToolSpec

TOOL_CALL_SCRIPT = json.dumps(
    {"thought": "需要先发送生产环境部署通知", "tool": "notify", "args": {"channel": "#ops", "message": "部署 v2.3.0"}}
)


class FakeModel(ModelPort):
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, tools) -> ModelOutput:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ModelOutput(self.script[idx])


class EchoFinalModel(ModelPort):
    """resume 之后只会被问一次：把最后一条真实消息内容回显进最终答案。
    approve/reject 两个分支用同一份模型代码，答案不同是因为状态真的不同。"""

    def complete(self, messages, tools) -> ModelOutput:
        return ModelOutput(json.dumps({"thought": "回显", "final": f"结果: {messages[-1].content}"}))


class NotifyTool(ToolPort):
    def __init__(self) -> None:
        self.calls = 0

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec("notify", "发送通知", {})]

    def call(self, name: str, args: dict) -> str:
        self.calls += 1
        return f"已发送到 {args.get('channel')}：{args.get('message')}"


def crash_approval(_call) -> bool:
    raise SystemExit("模拟进程崩溃：审批还没做决定")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(str(Path(tmp) / "runs"))

        print("=== 第一阶段：跑到工具提案，模拟进程在审批期间崩溃 ===")
        kernel1 = AgentKernel(
            model=FakeModel([TOOL_CALL_SCRIPT]),
            tools=NotifyTool(),
            planner=ReactPlanner(),
            checkpoints=store,
            approval=crash_approval,
            max_steps=4,
        )
        try:
            kernel1.run("请通知 #ops 频道正在部署 v2.3.0", state=RunState(run_id="deploy-run"))
        except SystemExit as exc:
            print(f"[崩溃] {exc}")
        print(f"checkpoint 已落盘: runs/deploy-run/turn_001_step_001.json（paused，待审批工具: notify）")

        print("\n=== 第二阶段：从同一个 fork 点分出批准/拒绝两条分支 ===")
        state_approve = fork(store, "deploy-run", "turn_001_step_001", new_run_id="deploy-approve")
        state_reject = fork(store, "deploy-run", "turn_001_step_001", new_run_id="deploy-reject")
        print(f"fork -> {state_approve.run_id}（forked_from={state_approve.forked_from}）")
        print(f"fork -> {state_reject.run_id}（forked_from={state_reject.forked_from}）")

        notify_approve = NotifyTool()
        kernel_approve = AgentKernel(
            model=EchoFinalModel(),
            tools=notify_approve,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: True,
            max_steps=4,
        )
        result_approve = kernel_approve.resume(state_approve)

        notify_reject = NotifyTool()
        kernel_reject = AgentKernel(
            model=EchoFinalModel(),
            tools=notify_reject,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: False,
            max_steps=4,
        )
        result_reject = kernel_reject.resume(state_reject)

        print("\n=== 结果对比 ===")
        print(f"[批准分支 {result_approve.run_id}] 工具实际执行次数={notify_approve.calls}")
        print(f"  状态={result_approve.status}  答案={result_approve.answer}")
        print(f"[拒绝分支 {result_reject.run_id}] 工具实际执行次数={notify_reject.calls}")
        print(f"  状态={result_reject.status}  答案={result_reject.answer}")

        source = store.load("deploy-run")
        print(f"\n源 run（deploy-run）fork 后仍是: status={source.status}, "
              f"pending_tool={source.pending_tool.name if source.pending_tool else None}（未被 fork 影响）")

        ok = (
            result_approve.status == "done"
            and result_reject.status == "done"
            and notify_approve.calls == 1
            and notify_reject.calls == 0
            and result_approve.answer != result_reject.answer
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
