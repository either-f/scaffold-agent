"""Fork 分支执行验证（ADR-0009）：同一个 fork 点，交给两个用不同 approval
回调构造的 AgentKernel 分别 resume，批准分支和拒绝分支要产生真实不同的结果——
不是两套写死的脚本，是同一份"回显最后一条消息"的模型代码在不同状态下的真实反应。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.fork import fork
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.types import Message, ModelOutput, RunState, ToolSpec

TOOL_CALL_SCRIPT = json.dumps({"thought": "发送通知", "tool": "notify", "args": {"to": "ops"}})


class FakeModel(ModelPort):
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, tools) -> ModelOutput:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ModelOutput(self.script[idx])


class EchoFinalModel(ModelPort):
    """resume 之后模型只会被问一次：把最后一条真实消息内容回显进最终答案。
    approve/reject 两个分支用同一份模型代码，答案不同是因为状态真的不同。"""

    def complete(self, messages, tools) -> ModelOutput:
        last = messages[-1].content
        return ModelOutput(json.dumps({"thought": "回显", "final": f"结果: {last}"}))


class CountingTool(ToolPort):
    def __init__(self, counter: list[int]) -> None:
        self.counter = counter

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec("notify", "发送通知", {})]

    def call(self, name: str, args: dict) -> str:
        self.counter.append(len(self.counter) + 1)
        return f"notified-{len(self.counter)}"


def crash_approval(_call) -> bool:
    raise SystemExit("模拟进程崩溃：审批还没做决定")


def run_fork_scenario() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(str(Path(tmp) / "runs"))
        counter_src: list[int] = []

        kernel1 = AgentKernel(
            model=FakeModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter_src),
            planner=ReactPlanner(),
            checkpoints=store,
            approval=crash_approval,
            max_steps=4,
        )
        crashed = False
        try:
            kernel1.run("请发送通知", state=RunState(run_id="fork-src"))
        except SystemExit:
            crashed = True

        source_before = store.load("fork-src")

        state_a = fork(store, "fork-src", "turn_001_step_001", new_run_id="fork-approve")
        state_b = fork(store, "fork-src", "turn_001_step_001", new_run_id="fork-reject")

        # resume() 会原地修改传入的 RunState（追加消息），必须在 resume 之前
        # 就把 fork 点的状态记下来，不然分叉之后两边消息天然不同，这个断言就失真了
        fork_point_matches = (
            state_a.run_id != "fork-src"
            and state_b.run_id != "fork-src"
            and state_a.run_id != state_b.run_id
            and state_a.messages == state_b.messages
            and state_a.forked_from == "fork-src@turn_001_step_001"
            and state_b.forked_from == "fork-src@turn_001_step_001"
        )

        counter_a: list[int] = []
        kernel_a = AgentKernel(
            model=EchoFinalModel(),
            tools=CountingTool(counter_a),
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: True,
            max_steps=4,
        )
        result_a = kernel_a.resume(state_a)

        counter_b: list[int] = []
        kernel_b = AgentKernel(
            model=EchoFinalModel(),
            tools=CountingTool(counter_b),
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: False,
            max_steps=4,
        )
        result_b = kernel_b.resume(state_b)

        source_after = store.load("fork-src")

        branch_a_ok = (
            result_a.status == "done" and counter_a == [1] and "notified-1" in (result_a.answer or "")
        )
        branch_b_ok = (
            result_b.status == "done"
            and counter_b == []
            and "否决了工具调用" in (result_b.answer or "")
        )
        diverged = result_a.answer != result_b.answer
        source_untouched = (
            source_before is not None
            and source_after is not None
            and source_before.status == source_after.status == "paused"
            and source_before.pending_tool == source_after.pending_tool
            and source_before.messages == source_after.messages
        )

        ok = (
            crashed
            and fork_point_matches
            and branch_a_ok
            and branch_b_ok
            and diverged
            and source_untouched
        )
        return {
            "crashed": crashed,
            "fork_point_matches": fork_point_matches,
            "branch_a": {"status": result_a.status, "answer": result_a.answer, "counter": counter_a},
            "branch_b": {"status": result_b.status, "answer": result_b.answer, "counter": counter_b},
            "diverged": diverged,
            "source_untouched": source_untouched,
            "ok": ok,
        }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_fork_scenario()
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["suite"] = "fork"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
