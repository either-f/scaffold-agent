"""WorkerDelegationPort 单测：AgentKernel worker（原语义）与 Worker 协议 worker（M10 新增
的 agentscope 互操作口子）两条路径都要跑通，离线、不依赖真实模型。

运行：PYTHONPATH=src python3 tests/test_worker_delegation.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.tools.agents import WorkerDelegationPort
from agent_kernel.adapters.tools.local import LocalToolbox
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner


class FakeProtocolWorker:
    """最小 Worker 协议实现：run(task) -> str，不涉及 agentscope。"""

    def __init__(self, answer: str | None = None, raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises
        self.calls: list[str] = []

    def run(self, task: str) -> str:
        self.calls.append(task)
        if self.raises:
            raise self.raises
        return self.answer or ""


def _agent_kernel_worker(final_answer: str) -> AgentKernel:
    model = FakeScriptedModel([f'{{"thought": "done", "final": "{final_answer}"}}'])
    return AgentKernel(model=model, tools=LocalToolbox(), planner=ReactPlanner(), max_steps=2)


def test_agent_kernel_worker_path_unchanged():
    delegation = WorkerDelegationPort(LocalToolbox())
    delegation.register("echo", _agent_kernel_worker("hello from kernel worker"))
    result = delegation.call("worker_echo", {"task": "say hi"})
    assert result.content == "hello from kernel worker"


def test_protocol_worker_path_returns_answer():
    fake = FakeProtocolWorker(answer="hello from protocol worker")
    delegation = WorkerDelegationPort(LocalToolbox())
    delegation.register("proto", fake)
    result = delegation.call("worker_proto", {"task": "say hi"})
    assert result.content == "hello from protocol worker"
    assert fake.calls == ["say hi"]


def test_protocol_worker_empty_answer_raises():
    fake = FakeProtocolWorker(answer="")
    delegation = WorkerDelegationPort(LocalToolbox())
    delegation.register("proto", fake)
    try:
        delegation.call("worker_proto", {"task": "say hi"})
        assert False, "应该抛出 RuntimeError"
    except RuntimeError:
        pass


def test_protocol_worker_exception_propagates():
    fake = FakeProtocolWorker(raises=ValueError("boom"))
    delegation = WorkerDelegationPort(LocalToolbox())
    delegation.register("proto", fake)
    try:
        delegation.call("worker_proto", {"task": "say hi"})
        assert False, "应该抛出 ValueError"
    except ValueError:
        pass


def test_unknown_worker_raises_key_error():
    delegation = WorkerDelegationPort(LocalToolbox())
    try:
        delegation.call("worker_ghost", {"task": "x"})
        assert False, "应该抛出 KeyError"
    except KeyError:
        pass


def test_inner_tool_still_passthrough():
    inner = LocalToolbox()
    inner.register("now", "返回当前时间", lambda: "2026-01-01")
    delegation = WorkerDelegationPort(inner)
    delegation.register("proto", FakeProtocolWorker(answer="ignored"))
    result = delegation.call("now", {})
    assert result.content == "2026-01-01"


if __name__ == "__main__":
    test_agent_kernel_worker_path_unchanged()
    test_protocol_worker_path_returns_answer()
    test_protocol_worker_empty_answer_raises()
    test_protocol_worker_exception_propagates()
    test_unknown_worker_raises_key_error()
    test_inner_tool_still_passthrough()
    print("OK: WorkerDelegationPort 测试全部通过")
