"""Effect Ledger 测试：跨轮 effect_id 不冲突、参数哈希校验、崩溃恢复三态
（回放 / 幂等重试 / 非幂等拒绝）。对应 ADR-0008 与本次 effect_id 冲突修复。

运行：PYTHONPATH=src python3 tests/test_effects.py   （也兼容 pytest）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import json

import pytest

from agent_kernel.adapters.effects import SqliteEffectLedger
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import (
    AgentKernel,
    ApprovalRequiredError,
    EffectArgumentMismatchError,
    EffectUnresolvedError,
)
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import (
    ArtifactRef,
    Message,
    ModelOutput,
    RetryPolicy,
    RunState,
    ToolEffectPolicy,
    ToolResult,
    ToolSpec,
    hash_arguments,
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


# --------------------------------------------------------------------------- #
# SPEC-80 新增：fork 隔离 / tool_name+run_id 校验 / requires_approval fail-closed /
# rejected 终态 / rowcount 校验 / 状态机 / 回放保留 artifacts
# --------------------------------------------------------------------------- #
from agent_kernel.adapters.effects import EffectNotFoundError, IllegalEffectTransitionError
from agent_kernel.fork import fork


class TwoNoArgTool(ToolPort):
    """两个零参工具：now 和 ping。hash_arguments({}) 相同，用于证明
    光靠 arguments_hash 拦不住零参工具间的 effect_id 碰撞。"""

    def __init__(self):
        self.calls = {"now": 0, "ping": 0}

    def list_tools(self):
        return [
            ToolSpec("now", "当前时间", {}),
            ToolSpec("ping", "探活", {}),
        ]

    def call(self, name, args):
        self.calls[name] += 1
        return ToolResult(content=f"{name}-ok")


def test_fork_branches_execute_independently():
    """SPEC-80 验收点1：同一 paused checkpoint 的两个 fork（批准 vs 拒绝），
    各自独立执行工具调用，不互相串结果。"""

    def crash_approval(_call):
        raise SystemExit("crash")

    with tempfile.TemporaryDirectory() as tmp:
        ckpt = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        store = JsonCheckpointStore(ckpt)
        counter: list[int] = []

        # 第一阶段：跑到工具提案，模拟在审批期间崩溃（pending_tool 已落盘）。
        src_ledger = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter),
            planner=ReactPlanner(),
            checkpoints=store,
            effects=src_ledger,
            approval=crash_approval,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="fork-src"))
        src_ledger.close()
        source_effect = store.load("fork-src").pending_effect_id
        assert source_effect == "fork-src:1:1"

        # 两个分支用各自的 ledger 文件（独立账本），确保隔离。
        state_a = fork(store, "fork-src", "turn_001_step_001", new_run_id="fork-approve")
        state_b = fork(store, "fork-src", "turn_001_step_001", new_run_id="fork-reject")
        # fork 必须清掉 pending_effect_id，否则两个分支会共享源 run 的 effect 行。
        assert state_a.pending_effect_id is None
        assert state_b.pending_effect_id is None
        assert state_a.pending_tool is not None and state_b.pending_tool is not None

        ledger_a = SqliteEffectLedger(str(Path(tmp) / "effects_a.db"))
        ledger_b = SqliteEffectLedger(str(Path(tmp) / "effects_b.db"))
        counter_a: list[int] = []
        counter_b: list[int] = []
        kernel_a = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter_a),
            planner=ReactPlanner(),
            checkpoints=store,
            effects=ledger_a,
            approval=lambda call: True,
            max_steps=4,
        )
        result_a = kernel_a.resume(state_a)
        kernel_b = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter_b),
            planner=ReactPlanner(),
            checkpoints=store,
            effects=ledger_b,
            approval=lambda call: False,
            max_steps=4,
        )
        result_b = kernel_b.resume(state_b)
        ledger_a.close()
        ledger_b.close()

        # 批准分支真实执行一次；拒绝分支不执行；各自账本里有独立 effect 行。
        assert result_a.status == "done" and counter_a == [1]
        assert result_b.status == "done" and counter_b == []
        with SqliteEffectLedger(str(Path(tmp) / "effects_a.db")) as la:
            eff_a = la.get("fork-approve:1:1")
        with SqliteEffectLedger(str(Path(tmp) / "effects_b.db")) as lb:
            eff_b = lb.get("fork-reject:1:1")
        assert eff_a is not None and eff_a.status == "succeeded"
        assert eff_b is not None and eff_b.status == "rejected"
        # 源 run 的 effect 行没被任何分支改过（fork 后仍 proposed）。
        with SqliteEffectLedger(ledger_path) as lsrc:
            eff_src = lsrc.get("fork-src:1:1")
        assert eff_src is not None and eff_src.status == "proposed"


def test_tool_name_mismatch_raises():
    """SPEC-80 验收点2：零参工具 now/ping 的 hash_arguments({}) 相同，
    在账本里用 now 的 effect_id 塞一行，再用 ping 命中：必须被 tool_name 校验拦下。"""
    with tempfile.TemporaryDirectory() as tmp:
        ledger = SqliteEffectLedger(str(Path(tmp) / "effects.db"))
        from agent_kernel.types import Effect

        ledger.propose(Effect("collide-tool:1:1", "collide-tool", "now", hash_arguments({})))
        kernel = AgentKernel(
            model=ScriptedModel([json.dumps({"thought": "ping", "tool": "ping", "args": {}})]),
            tools=TwoNoArgTool(),
            planner=ReactPlanner(),
            effects=ledger,
            max_steps=4,
        )
        try:
            with pytest.raises(EffectArgumentMismatchError):
                kernel.run("ping", state=RunState(run_id="collide-tool"))
        finally:
            ledger.close()


def test_run_id_mismatch_raises():
    """SPEC-80 验收点2：effect.run_id 与 state.run_id 不一致（fork 串行的典型症状）
    也要被拦下。"""
    with tempfile.TemporaryDirectory() as tmp:
        ledger = SqliteEffectLedger(str(Path(tmp) / "effects.db"))
        from agent_kernel.types import Effect

        # 塞一行 run_id=other-run，但用当前 run 的 effect_id 命中。
        ledger.propose(
            Effect("this-run:1:1", "other-run", "send", hash_arguments({"to": "a@b.com"}))
        )
        kernel = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool([]),
            planner=ReactPlanner(),
            effects=ledger,
            max_steps=4,
        )
        try:
            with pytest.raises(EffectArgumentMismatchError):
                kernel.run("发个通知", state=RunState(run_id="this-run"))
        finally:
            ledger.close()


def test_requires_approval_fail_closed_without_approval():
    """SPEC-80 验收点3：工具声明 requires_approval=True，内核没配 approval 回调，
    必须在执行前抛 ApprovalRequiredError，绝不静默跑。"""
    with tempfile.TemporaryDirectory() as tmp:
        counter: list[int] = []
        policy = ToolEffectPolicy(requires_approval=True)
        ledger = SqliteEffectLedger(str(Path(tmp) / "effects.db"))
        kernel = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter, effect_policy=policy),
            planner=ReactPlanner(),
            effects=ledger,
            max_steps=4,
        )
        try:
            with pytest.raises(ApprovalRequiredError):
                kernel.run("发个通知", state=RunState(run_id="approval-run"))
            assert counter == []  # 工具确实没被执行
        finally:
            ledger.close()


def test_requires_approval_passes_when_approval_configured():
    """requires_approval=True 但内核配了 approval 回调：行为不变，正常走审批流。"""
    with tempfile.TemporaryDirectory() as tmp:
        counter: list[int] = []
        policy = ToolEffectPolicy(requires_approval=True)
        ledger = SqliteEffectLedger(str(Path(tmp) / "effects.db"))
        kernel = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT, FINAL_SCRIPT]),
            tools=CountingTool(counter, effect_policy=policy),
            planner=ReactPlanner(),
            effects=ledger,
            approval=lambda call: True,
            max_steps=4,
        )
        try:
            state = kernel.run("发个通知", state=RunState(run_id="approval-ok"))
            assert state.status == "done" and counter == [1]
        finally:
            ledger.close()


def test_rejected_effect_terminal_state():
    """SPEC-80 验收点4：HITL 否决后 effect 行落到 "rejected"，不是卡在 "proposed"。"""

    def crash_approval(_call):
        raise SystemExit("crash")

    with tempfile.TemporaryDirectory() as tmp:
        ckpt = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        store = JsonCheckpointStore(ckpt)
        counter: list[int] = []

        src_ledger = SqliteEffectLedger(ledger_path)
        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=CountingTool(counter),
            planner=ReactPlanner(),
            checkpoints=store,
            effects=src_ledger,
            approval=crash_approval,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="reject-run"))
        src_ledger.close()

        state = store.load("reject-run")
        ledger2 = SqliteEffectLedger(ledger_path)
        kernel2 = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=CountingTool(counter),
            planner=ReactPlanner(),
            checkpoints=store,
            effects=ledger2,
            approval=lambda call: False,
            max_steps=4,
        )
        kernel2.resume(state)
        eff = ledger2.get("reject-run:1:1")
        ledger2.close()
        assert eff is not None and eff.status == "rejected"


def test_mark_unknown_effect_id_raises():
    """SPEC-80 验收点5：对不存在的 effect_id 调 mark_* 必须抛，不能静默成功。"""
    with tempfile.TemporaryDirectory() as tmp:
        with SqliteEffectLedger(str(Path(tmp) / "effects.db")) as ledger:
            with pytest.raises(EffectNotFoundError):
                ledger.mark_succeeded("does-not-exist", "x")
            with pytest.raises(EffectNotFoundError):
                ledger.mark_approved("does-not-exist")
            with pytest.raises(EffectNotFoundError):
                ledger.mark_executing("does-not-exist")
            with pytest.raises(EffectNotFoundError):
                ledger.mark_failed("does-not-exist", "x")
            with pytest.raises(EffectNotFoundError):
                ledger.mark_rejected("does-not-exist")


def test_illegal_transition_raises():
    """SPEC-80 验收点5/6：succeeded -> executing 是非法迁移，必须抛。"""
    with tempfile.TemporaryDirectory() as tmp:
        with SqliteEffectLedger(str(Path(tmp) / "effects.db")) as ledger:
            from agent_kernel.types import Effect

            ledger.propose(Effect("tx:1", "tx", "send", hash_arguments({})))
            ledger.mark_approved("tx:1")
            ledger.mark_executing("tx:1")
            ledger.mark_succeeded("tx:1", "ok")
            # 终态后再迁移：非法
            with pytest.raises(IllegalEffectTransitionError):
                ledger.mark_executing("tx:1")
            with pytest.raises(IllegalEffectTransitionError):
                ledger.mark_succeeded("tx:1", "again")
            with pytest.raises(IllegalEffectTransitionError):
                ledger.mark_rejected("tx:1")


def test_replay_preserves_artifacts():
    """SPEC-80 验收点7：工具产生 ArtifactRef 后崩溃，恢复回放的 ToolResult
    仍带 artifacts，不是只剩扁平文本。"""
    with tempfile.TemporaryDirectory() as tmp:
        ckpt = str(Path(tmp) / "runs")
        ledger_path = str(Path(tmp) / "effects.db")
        counter: list[int] = []

        class ArtifactTool(ToolPort):
            def __init__(self):
                self.calls = 0

            def list_tools(self):
                return [ToolSpec("send", "有副作用", {}, effect_policy=ToolEffectPolicy())]

            def call(self, name, args):
                self.calls += 1
                return ToolResult(
                    content="sent-with-big-payload",
                    artifacts=[
                        ArtifactRef(uri="file:///runs/artifacts/abc.txt", mime_type="text/plain", description="完整结果")
                    ],
                )

        store1 = JsonCheckpointStore(ckpt)
        real_ledger = SqliteEffectLedger(ledger_path)
        original_mark_succeeded = real_ledger.mark_succeeded

        def crashing_mark_succeeded(effect_id, result_ref):
            original_mark_succeeded(effect_id, result_ref)
            raise SystemExit("账本已 succeeded，checkpoint 没落盘")

        real_ledger.mark_succeeded = crashing_mark_succeeded
        kernel1 = AgentKernel(
            model=ScriptedModel([TOOL_CALL_SCRIPT]),
            tools=ArtifactTool(),
            planner=ReactPlanner(),
            checkpoints=store1,
            effects=real_ledger,
            max_steps=4,
        )
        with pytest.raises(SystemExit):
            kernel1.run("发个通知", state=RunState(run_id="art-run"))
        real_ledger.mark_succeeded = original_mark_succeeded
        real_ledger.close()

        # 回放后从消息历史里验证 artifacts 被还原（_finish_tool 会把 artifact uri 拼进 tool 消息文本）。
        store2 = JsonCheckpointStore(ckpt)
        ledger2 = SqliteEffectLedger(ledger_path)
        state = store2.load("art-run")
        kernel2 = AgentKernel(
            model=ScriptedModel([FINAL_SCRIPT]),
            tools=ArtifactTool(),
            planner=ReactPlanner(),
            checkpoints=store2,
            effects=ledger2,
            max_steps=4,
        )
        final_state = kernel2.resume(state)
        ledger2.close()
        tool_msgs = "".join(m.content for m in final_state.messages if m.role == "tool")
        assert "file:///runs/artifacts/abc.txt" in tool_msgs  # artifact uri 被回放重建
        assert "sent-with-big-payload" in tool_msgs


if __name__ == "__main__":
    test_effect_id_scoped_by_turn_not_just_step()
    test_argument_hash_mismatch_raises()
    test_executing_not_idempotent_blocks_resume()
    test_executing_idempotent_retries_on_resume()
    test_succeeded_effect_replays_without_reexecuting()
    test_fork_branches_execute_independently()
    test_tool_name_mismatch_raises()
    test_run_id_mismatch_raises()
    test_requires_approval_fail_closed_without_approval()
    test_requires_approval_passes_when_approval_configured()
    test_rejected_effect_terminal_state()
    test_mark_unknown_effect_id_raises()
    test_illegal_transition_raises()
    test_replay_preserves_artifacts()
    print("OK: effect ledger 测试全部通过")
