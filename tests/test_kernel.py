"""内核状态机冒烟测试：主循环 / HITL / 恢复的非法状态迁移 / 多轮 turn 语义。

运行：PYTHONPATH=src python3 tests/test_kernel.py   （也兼容 pytest）
"""
import sys
import tempfile

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_local import LocalToolbox, default_toolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import RunState

SCRIPT = [
    '{"thought": "t", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
    '{"thought": "t", "final": "结果是 49"}',
]


def test_kernel_loop_and_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        kernel = AgentKernel(
            model=FakeScriptedModel(list(SCRIPT)),
            tools=default_toolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
        )
        state = kernel.run("算一下 (3+4)*7")
        assert state.status == "done"
        assert "49" in (state.answer or "")
        assert any(m.role == "tool" and m.content == "49" for m in state.messages)

        loaded = store.load(state.run_id)
        assert loaded is not None and loaded.answer == state.answer


def test_hitl_veto():
    calls = []
    tools = LocalToolbox()
    tools.register("calc", "calc", lambda expression: calls.append(expression) or "49")
    kernel = AgentKernel(
        model=FakeScriptedModel(list(SCRIPT)),
        tools=tools,
        planner=ReactPlanner(),
        approval=lambda call: False,  # 全部否决
    )
    state = kernel.run("算一下 (3+4)*7")
    assert calls == []
    assert any("否决" in m.content for m in state.messages if m.role == "tool")


def _crash_approval(_call) -> bool:
    """approval 内核是同步的：正常审批回调会在同一次 run() 调用里立刻决定并继续跑完，
    永远不会真的把 paused 状态持久化到磁盘。模拟"审批期间进程崩溃"（SystemExit
    逃出调用栈）才能造出一个真实停在 paused 的 checkpoint 给 resume() 测。"""
    raise SystemExit("模拟进程崩溃：审批还没做决定")


def test_resume_pending_approval():
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        tools = LocalToolbox()
        tools.register("calc", "calc", lambda expression: calls.append(expression) or "49")
        store = JsonCheckpointStore(tmp)
        crashing_kernel = AgentKernel(
            model=FakeScriptedModel(list(SCRIPT)),
            tools=tools,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=_crash_approval,
        )
        with pytest.raises(SystemExit):
            crashing_kernel.run("算一下 (3+4)*7", state=RunState(run_id="hitl-1"))
        assert calls == []  # 崩溃前不能已经执行

        loaded = store.load("hitl-1")
        assert loaded is not None and loaded.status == "paused"

        resuming_kernel = AgentKernel(
            # resume 不会为已经决定过的 pending tool 重新问模型；
            # 下一次 complete() 问的是"工具执行完之后"那一步，脚本只需要 final。
            model=FakeScriptedModel([SCRIPT[1]]),
            tools=tools,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: True,
        )
        resumed = resuming_kernel.resume(loaded)
        assert resumed.status == "done"
        assert calls == ["(3+4)*7"]  # 只执行一次


def test_run_cannot_append_to_running_state():
    kernel = AgentKernel(
        model=FakeScriptedModel(list(SCRIPT)),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        approval=lambda call: True,  # 强制停在 paused，但用一个 running 且已有消息的 state 直接喂 run()
    )
    state = RunState()
    state.add("user", "已经有过一条消息了")
    with pytest.raises(ValueError):
        kernel.run("再来一条", state)


def test_resume_rejects_terminal_states():
    kernel = AgentKernel(model=FakeScriptedModel([]), tools=default_toolbox(), planner=ReactPlanner())
    done_state = RunState(status="done")
    with pytest.raises(ValueError):
        kernel.resume(done_state)
    failed_state = RunState(status="failed")
    with pytest.raises(ValueError):
        kernel.resume(failed_state)


def test_resume_paused_without_approval_callback_rejected():
    kernel_no_approval = AgentKernel(model=FakeScriptedModel([]), tools=default_toolbox(), planner=ReactPlanner())
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        crashing_kernel = AgentKernel(
            model=FakeScriptedModel(list(SCRIPT)),
            tools=default_toolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
            approval=_crash_approval,
        )
        with pytest.raises(SystemExit):
            crashing_kernel.run("算一下 (3+4)*7", state=RunState(run_id="hitl-2"))
        loaded = store.load("hitl-2")
        assert loaded is not None and loaded.status == "paused"
        with pytest.raises(PermissionError):
            kernel_no_approval.resume(loaded)


def test_max_steps_exhausted():
    loop_script = ['{"thought": "t", "tool": "calc", "args": {"expression": "1+1"}}'] * 5
    kernel = AgentKernel(
        model=FakeScriptedModel(loop_script),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        max_steps=2,
    )
    state = kernel.run("一直调用工具")
    assert state.status == "failed"
    assert state.step >= 2


def test_multi_turn_resets_step_but_keeps_run_id():
    kernel = AgentKernel(
        model=FakeScriptedModel(['{"thought": "t", "final": "第一轮完成"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    state = kernel.run("第一轮")
    assert state.status == "done" and state.turn == 1 and state.step == 1

    kernel2 = AgentKernel(
        model=FakeScriptedModel(['{"thought": "t", "final": "第二轮完成"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    state2 = kernel2.run("第二轮", state)
    assert state2.run_id == state.run_id
    assert state2.turn == 2 and state2.step == 1  # step 在新一轮清零，run_id 不变


if __name__ == "__main__":
    test_kernel_loop_and_checkpoint()
    test_hitl_veto()
    test_resume_pending_approval()
    test_run_cannot_append_to_running_state()
    test_resume_rejects_terminal_states()
    test_resume_paused_without_approval_callback_rejected()
    test_max_steps_exhausted()
    test_multi_turn_resets_step_but_keeps_run_id()
    print("OK: kernel 状态机测试全部通过")
