"""冒烟测试：内核循环 / 工具调用 / HITL 否决 / checkpoint 读回。

运行：PYTHONPATH=src python3 tests/test_smoke.py   （也兼容 pytest）
"""
import sys
import tempfile

sys.path.insert(0, "src")

from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_local import LocalToolbox, default_toolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import RunState, ToolCall

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


def test_resume_pending_approval():
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        tools = LocalToolbox()
        tools.register("calc", "calc", lambda expression: calls.append(expression) or "49")
        store = JsonCheckpointStore(tmp)
        original = RunState()
        kernel = AgentKernel(
            model=FakeScriptedModel([SCRIPT[0]]),
            tools=tools,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        try:
            kernel.run("算一下 (3+4)*7", original)
            raise AssertionError("approval 中断应传播")
        except KeyboardInterrupt:
            pass

        paused = store.load(original.run_id)
        assert paused is not None and paused.status == "paused"
        assert paused.pending_tool is not None and paused.pending_tool.name == "calc"
        assert calls == []

        resumed = AgentKernel(
            model=FakeScriptedModel([SCRIPT[1]]),
            tools=tools,
            planner=ReactPlanner(),
            checkpoints=store,
            approval=lambda call: True,
        ).resume(paused)
        assert resumed.status == "done"
        assert calls == ["(3+4)*7"]
        assert sum(m.role == "user" for m in resumed.messages) == 1
        assert sum(m.role == "tool" for m in resumed.messages) == 1
        assert not list((store.root / resumed.run_id).glob("*.tmp"))


def test_resume_guards_and_pending_at_step_limit():
    legacy = RunState.from_dict(
        {"run_id": "legacy", "messages": [], "step": 1, "status": "running", "answer": None}
    )
    assert legacy.pending_tool is None

    kernel = AgentKernel(
        model=FakeScriptedModel([]),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    for state in (RunState(status="done"), RunState(status="failed"), RunState(status="paused")):
        try:
            kernel.resume(state)
            raise AssertionError(f"状态应拒绝恢复: {state.status}")
        except (ValueError, PermissionError):
            pass

    paused = RunState(status="paused", pending_tool=ToolCall("calc", {"expression": "1+1"}))
    try:
        kernel.resume(paused)
        raise AssertionError("待审批恢复必须 fail-closed")
    except PermissionError:
        pass

    calls = []
    tools = LocalToolbox()
    tools.register("calc", "calc", lambda expression: calls.append(expression) or "2")
    pending = RunState(step=1, pending_tool=ToolCall("calc", {"expression": "1+1"}))
    result = AgentKernel(
        model=FakeScriptedModel([]),
        tools=tools,
        planner=ReactPlanner(),
        max_steps=1,
    ).resume(pending)
    assert calls == ["1+1"]
    assert result.pending_tool is None and result.status == "failed"


if __name__ == "__main__":
    test_kernel_loop_and_checkpoint()
    test_hitl_veto()
    test_resume_pending_approval()
    test_resume_guards_and_pending_at_step_limit()
    print("OK: 全部冒烟测试通过")
