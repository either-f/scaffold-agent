"""冒烟测试：内核循环 / 工具调用 / HITL 否决 / checkpoint 读回。

运行：PYTHONPATH=src python3 tests/test_smoke.py   （也兼容 pytest）
"""
import sys
import tempfile

sys.path.insert(0, "src")

from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_local import default_toolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner

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
    kernel = AgentKernel(
        model=FakeScriptedModel(list(SCRIPT)),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        approval=lambda call: False,  # 全部否决
    )
    state = kernel.run("算一下 (3+4)*7")
    assert any("否决" in m.content for m in state.messages if m.role == "tool")


if __name__ == "__main__":
    test_kernel_loop_and_checkpoint()
    test_hitl_veto()
    print("OK: 全部冒烟测试通过")
