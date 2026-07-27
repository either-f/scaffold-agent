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
from agent_kernel.ports import MemoryPort
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
    assert legacy.turn == 0 and legacy.context_summary == ""
    assert legacy.summarized_message_count == 0

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


def test_multi_turn_and_checkpoint_compatibility():
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        kernel = AgentKernel(
            model=FakeScriptedModel(
                [
                    '{"thought": "t", "final": "第一轮"}',
                    '{"thought": "t", "final": "第二轮"}',
                ]
            ),
            tools=default_toolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
        )
        state = kernel.run("第一问")
        run_id = state.run_id
        assert state.turn == 1 and state.step == 1

        state = kernel.run("第二问", state)
        assert state.run_id == run_id
        assert state.turn == 2 and state.step == 1 and state.answer == "第二轮"
        assert [message.content for message in state.messages if message.role == "user"] == [
            "第一问",
            "第二问",
        ]
        run_dir = store.root / run_id
        assert (run_dir / "turn_001_step_001.json").exists()
        assert (run_dir / "turn_002_step_001.json").exists()
        latest = store.load(run_id)
        assert latest is not None and latest.turn == 2 and latest.answer == "第二轮"

    for status in ("paused", "failed"):
        try:
            kernel.run("不应追加", RunState(status=status))
            raise AssertionError(f"状态应拒绝续聊: {status}")
        except ValueError:
            pass


def test_planner_retrieves_by_latest_user_message():
    class RecordingMemory(MemoryPort):
        def __init__(self):
            self.query = None
            self.k = None

        def add(self, run_id, role, content):
            pass

        def search(self, query, k=5):
            self.query, self.k = query, k
            return ["最新问题", "本轮已有内容", "跨会话记忆"]

    class RecordingModel(FakeScriptedModel):
        def complete(self, messages, tools):
            self.messages = messages
            return super().complete(messages, tools)

    state = RunState()
    state.add("user", "旧问题")
    state.add("tool", "工具结果", "demo")
    state.add("assistant", "本轮已有内容")
    state.add("user", "最新问题")
    memory = RecordingMemory()
    model = RecordingModel(['{"thought": "t", "final": "ok"}'])

    ReactPlanner().step(state, model, default_toolbox(), memory)

    assert (memory.query, memory.k) == ("最新问题", 8)
    assert "相关记忆：\n- 跨会话记忆" in model.messages[0].content
    assert "- 最新问题" not in model.messages[0].content
    assert "- 本轮已有内容" not in model.messages[0].content


if __name__ == "__main__":
    test_kernel_loop_and_checkpoint()
    test_hitl_veto()
    test_resume_pending_approval()
    test_resume_guards_and_pending_at_step_limit()
    test_multi_turn_and_checkpoint_compatibility()
    test_planner_retrieves_by_latest_user_message()
    print("OK: 全部冒烟测试通过")
