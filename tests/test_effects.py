"""Effect Ledger 测试：跨轮 effect_id 不冲突、参数哈希校验、崩溃恢复三态
（回放 / 幂等重试 / 非幂等拒绝）。对应 ADR-0008 与本次 effect_id 冲突修复。

运行：PYTHONPATH=src python3 tests/test_effects.py   （也兼容 pytest）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.effects import SqliteEffectLedger
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel, EffectArgumentMismatchError, EffectUnresolvedError
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import (
    Message,
    ModelOutput,
    RetryPolicy,
    RunState,
    ToolEffectPolicy,
    ToolResult,
    ToolSpec,
)


class ScriptedModel(ModelPort):
    """按顺序回放脚本化 JSON 动作；用完了就一直返回最后一条。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, tools):
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ModelOutput(self.script[idx])


class CountingTool(ToolPort):
    def __init__(self, counter, effect_policy=None, crash_once=False):
        self.counter = counter
        self.effect_policy = effect_policy
        self._crash_pending = crash_once

    def list_tools(self):
        return [ToolSpec("send", "有副作用的操作", {}, effect_policy=self.effect_policy)]

    def call(self, name, args):
        self.counter.append(len(self.counter) + 1)
        if self._crash_pending:
            self._crash_pending = False
            raise SystemExit("模拟进程崩溃：副作用已发生，尚未确认")
        return ToolResult(content=f"sent-{len(self.counter)}")


TOOL_CALL_SCRIPT = '{"thought": "发送", "tool": "send", "args": {"to": "a@b.com"}}'
FINAL_SCRIPT = '{"thought": "完成", "final": "已发送"}'


def test_effect_id_scoped_by_turn_not_just_step():
    """同一个 run_id 换轮后 step 会清零；effect_id 必须带 turn，否则跨轮撞车。"""
    with tempfile.TemporaryDirectory() as tmp:
        counter = []
        with SqliteEffectLedger(str(Path(tmp) / "effects.db")) as ledger:
            kernel = AgentKernel(
                model=ScriptedModel([TOOL_CALL_SCRIPT, FINAL_SCRIPT, TOOL_CALL_SCRIPT, FINAL_SCRIPT]),
                tools=CountingTool(counter),
                planner=ReactPlanner(),
                effects=ledger,
                max_steps=4,
            )
            state = kernel.run("发个通知", state=RunState(run_id="turn-scope"))
            assert state.status == "done"
            assert ledger.get("turn-scope:1:1") is not None  # turn=1, step=1

            state2 = kernel.run("再发一次", state)  # 第二轮：step 清零回 0/1，turn=2
            assert state2.status == "done"
            assert ledger.get("turn-scope:2:1") is not None  # 不再是 "turn-scope:1"，不撞第一轮
            assert counter == [1, 2]  # 两轮各真实执行了一次，没有互相当成重复


def test_argument_hash_mismatch_raises():
    """人为伪造一个 effect_id 命中、但参数哈希对不上的账本记录：
    模拟 effect_id 冲突场景，内核必须拒绝自动回放/重试。"""
    with tempfile.TemporaryDirectory() as tmp:
        ledger = SqliteEffectLedger(str(Path(tmp) / "effects.db"))
        counter = []
        kernel = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter),
            planner=ReactPlanner(),
            effects=ledger,
            max_steps=4,
        )
        # 抢先在账本里塞一条同 effect_id、不同参数哈希的记录，模拟冲突。
        from agent_kernel.types import Effect

        state = RunState(run_id="collide")
        ledger.propose(Effect("collide:1:1", "collide", "send", "deliberately-wrong-hash"))
        try:
            with pytest.raises(EffectArgumentMismatchError):
                kernel.run("发个通知", state=state)
        finally:
            ledger.close()


def test_executing_not_idempotent_blocks_resume():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter = []
        policy = ToolEffectPolicy(idempotent=False)

        store1 = JsonCheckpointStore(ckpt_path)
        ledger1 = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=True),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=ledger1,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="scn-1"))
        ledger1.close()

        with SqliteEffectLedger(ledger_path) as inspect_ledger:
            effect_after_crash = inspect_ledger.get("scn-1:1:1")
        assert effect_after_crash is not None and effect_after_crash.status == "executing"

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("scn-1")
        kernel2 = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        with pytest.raises(EffectUnresolvedError) as exc_info:
            kernel2.resume(state)
        assert exc_info.value.effect_id == "scn-1:1:1"
        assert counter == [1]  # 没有被重复执行
        ledger2.close()


def test_executing_idempotent_retries_on_resume():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter = []
        policy = ToolEffectPolicy(idempotent=True, retry_policy=RetryPolicy(max_attempts=2))

        store1 = JsonCheckpointStore(ckpt_path)
        ledger1 = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=True),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=ledger1,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="scn-2"))
        ledger1.close()

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("scn-2")
        kernel2 = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        final_state = kernel2.resume(state)
        assert final_state.status == "done"
        assert counter == [1, 2]  # 安全地自动重试了一次
        ledger2.close()


def test_succeeded_effect_replays_without_reexecuting():
    """工具已成功、_finish_tool 的 checkpoint 还没落盘时崩溃：恢复必须回放而不是重跑。"""
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_path = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter = []
        policy = ToolEffectPolicy(idempotent=False)

        store1 = JsonCheckpointStore(ckpt_path)
        real_ledger = SqliteEffectLedger(ledger_path)
        original_mark_succeeded = real_ledger.mark_succeeded

        def crashing_mark_succeeded(effect_id, result_ref):
            original_mark_succeeded(effect_id, result_ref)
            raise SystemExit("模拟进程崩溃：账本已确认 succeeded，checkpoint 还没落盘")

        real_ledger.mark_succeeded = crashing_mark_succeeded

        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=real_ledger,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="scn-3"))
        real_ledger.mark_succeeded = original_mark_succeeded
        real_ledger.close()

        store2 = JsonCheckpointStore(ckpt_path)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("scn-3")
        kernel2 = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter, policy, crash_once=False),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        final_state = kernel2.resume(state)
        assert final_state.status == "done"
        assert counter == [1]  # 核心断言：没有被重复执行，是回放
        assert "sent-1" in "".join(m.content for m in final_state.messages)
        ledger2.close()


if __name__ == "__main__":
    test_effect_id_scoped_by_turn_not_just_step()
    test_argument_hash_mismatch_raises()
    test_executing_not_idempotent_blocks_resume()
    test_executing_idempotent_retries_on_resume()
    test_succeeded_effect_replays_without_reexecuting()
    print("OK: effect ledger 测试全部通过")
