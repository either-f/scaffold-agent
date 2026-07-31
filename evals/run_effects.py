"""Effect Ledger 崩溃恢复验证（ADR-0008）：证明"状态能恢复"和"副作用不重复"是两件事。

三个场景，每个都用两个独立的 AgentKernel 实例（分别代表崩溃前/恢复后进程），
共享同一份磁盘上的 CheckpointStore + EffectLedger 文件。用 `raise SystemExit`
在工具真正产生副作用之后立刻中断（SystemExit 是 BaseException，`_execute_tool`
的 `except Exception` 抓不住，真实逃出调用栈），模拟"副作用已发生、进程死了"。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.effects import SqliteEffectLedger
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel, EffectUnresolvedError
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import Message, ModelOutput, RetryPolicy, RunState, ToolEffectPolicy, ToolSpec


class FakeModel(ModelPort):
    """按顺序回放脚本化 JSON 动作；用完了就一直返回最后一条。"""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[Message], tools) -> ModelOutput:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ModelOutput(self.script[idx])


class CrashingCounterTool(ToolPort):
    """calc 工具：调用即 counter.append(1)；crash_once=True 时第一次调用完副作用后
    立刻 raise SystemExit，模拟"副作用已发生、checkpoint/账本还没确认"。"""

    def __init__(self, counter: list[int], effect_policy: ToolEffectPolicy | None, crash_once: bool) -> None:
        self.counter = counter
        self.effect_policy = effect_policy
        self._crash_pending = crash_once

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec("send", "有副作用的操作", {}, effect_policy=self.effect_policy)]

    def call(self, name: str, args: dict) -> str:
        self.counter.append(len(self.counter) + 1)
        if self._crash_pending:
            self._crash_pending = False
            raise SystemExit("模拟进程崩溃：副作用已发生，尚未确认")
        return f"sent-{len(self.counter)}"


TOOL_CALL_SCRIPT = json.dumps({"thought": "发送", "tool": "send", "args": {"to": "a@b.com"}})
FINAL_SCRIPT = json.dumps({"thought": "完成", "final": "已发送"})


def scenario_executing_not_idempotent() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter: list[int] = []
        policy = ToolEffectPolicy(idempotent=False)

        store1 = JsonCheckpointStore(ckpt_path)
        ledger1 = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=FakeModel([TOOL_CALL_SCRIPT]),
            tools=CrashingCounterTool(counter, policy, crash_once=True),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=ledger1,
            max_steps=4,
        )
        crashed = False
        try:
            kernel1.run("发个通知", state=RunState(run_id="scn-1"))
        except SystemExit:
            crashed = True
        ledger1.close()

        inspect_ledger = SqliteEffectLedger(ledger_path)
        effect_after_crash = inspect_ledger.get("scn-1:1")
        inspect_ledger.close()

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("scn-1")
        kernel2 = AgentKernel(
            model=FakeModel([FINAL_SCRIPT]),  # resume 不会为已经决定过的 pending tool 重新问模型
            tools=CrashingCounterTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        raised = None
        try:
            kernel2.resume(state)
        except EffectUnresolvedError as exc:
            raised = exc
        ledger2.close()

        ok = (
            crashed
            and effect_after_crash is not None
            and effect_after_crash.status == "executing"
            and effect_after_crash.attempt == 1
            and raised is not None
            and raised.effect_id == "scn-1:1"
            and raised.status == "executing"
            and counter == [1]  # 没有被重复执行
        )
        return {
            "name": "executing_not_idempotent",
            "crashed": crashed,
            "ledger_status_after_crash": effect_after_crash.status if effect_after_crash else None,
            "raised_effect_unresolved": raised is not None,
            "counter": counter,
            "ok": ok,
        }


def scenario_executing_idempotent_retry() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter: list[int] = []
        policy = ToolEffectPolicy(idempotent=True, retry_policy=RetryPolicy(max_attempts=2))

        store1 = JsonCheckpointStore(ckpt_path)
        ledger1 = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=FakeModel([TOOL_CALL_SCRIPT]),
            tools=CrashingCounterTool(counter, policy, crash_once=True),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=ledger1,
            max_steps=4,
        )
        try:
            kernel1.run("发个通知", state=RunState(run_id="scn-2"))
        except SystemExit:
            pass
        ledger1.close()

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("scn-2")
        kernel2 = AgentKernel(
            model=FakeModel([FINAL_SCRIPT]),  # resume 不会为已经决定过的 pending tool 重新问模型
            tools=CrashingCounterTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        final_state = kernel2.resume(state)
        effect_final = ledger2.get("scn-2:1")
        ledger2.close()

        ok = (
            final_state.status == "done"
            and counter == [1, 2]  # 安全地自动重试了一次
            and effect_final is not None
            and effect_final.status == "succeeded"
            and effect_final.attempt == 2
        )
        return {
            "name": "executing_idempotent_retry",
            "final_status": final_state.status,
            "counter": counter,
            "effect_attempt": effect_final.attempt if effect_final else None,
            "ok": ok,
        }


def scenario_succeeded_replay() -> dict:
    """对应最初 bug 报告的确切窗口：工具已成功，_finish_tool 的 checkpoint 还没落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter: list[int] = []
        policy = ToolEffectPolicy(idempotent=False)

        store1 = JsonCheckpointStore(ckpt_path)
        real_ledger = SqliteEffectLedger(ledger_path)

        # 包一层：真实 mark_succeeded 先正常提交，再模拟进程在这之后、
        # _finish_tool 的 checkpoint 落盘之前崩溃。
        original_mark_succeeded = real_ledger.mark_succeeded

        def crashing_mark_succeeded(effect_id: str, result_ref: str) -> None:
            original_mark_succeeded(effect_id, result_ref)
            raise SystemExit("模拟进程崩溃：账本已确认 succeeded，checkpoint 还没落盘")

        real_ledger.mark_succeeded = crashing_mark_succeeded  # type: ignore[method-assign]

        kernel1 = AgentKernel(
            model=FakeModel([TOOL_CALL_SCRIPT]),
            tools=CrashingCounterTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=real_ledger,
            max_steps=4,
        )
        crashed = False
        try:
            kernel1.run("发个通知", state=RunState(run_id="scn-3"))
        except SystemExit:
            crashed = True
        real_ledger.mark_succeeded = original_mark_succeeded  # type: ignore[method-assign]
        real_ledger.close()

        inspect_ledger = SqliteEffectLedger(ledger_path)
        effect_after_crash = inspect_ledger.get("scn-3:1")
        inspect_ledger.close()
        checkpoint_after_crash = JsonCheckpointStore(ckpt_path).load("scn-3")

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)  # 未包装的真实账本，重新打开同一文件
        state = store2.load("scn-3")
        kernel2 = AgentKernel(
            model=FakeModel([FINAL_SCRIPT]),
            tools=CrashingCounterTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        final_state = kernel2.resume(state)
        ledger2.close()

        ok = (
            crashed
            and effect_after_crash is not None
            and effect_after_crash.status == "succeeded"
            and effect_after_crash.result_ref == "sent-1"
            and checkpoint_after_crash is not None
            and checkpoint_after_crash.pending_tool is not None  # checkpoint 确实没来得及清 pending
            and final_state.status == "done"
            and "sent-1" in "".join(m.content for m in final_state.messages)
            and counter == [1]  # 核心断言：没有被重复执行，是回放
        )
        return {
            "name": "succeeded_replay",
            "crashed": crashed,
            "ledger_status_after_crash": effect_after_crash.status if effect_after_crash else None,
            "checkpoint_pending_tool_after_crash": checkpoint_after_crash.pending_tool is not None
            if checkpoint_after_crash
            else None,
            "final_status": final_state.status,
            "counter": counter,
            "ok": ok,
        }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios = [
        scenario_executing_not_idempotent(),
        scenario_executing_idempotent_retry(),
        scenario_succeeded_replay(),
    ]
    ok = all(s["ok"] for s in scenarios)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "effects",
        "scenarios": scenarios,
        "ok": ok,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
