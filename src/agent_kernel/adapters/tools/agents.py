"""worker 委派工具箱：ToolPort 装饰器，把 worker 暴露为带前缀的工具。

每个注册的 worker 暴露为一个 `worker_<name>` 工具，接受 `task` 字符串参数。
内层 ToolPort 的工具照常透传，与 SkillToolbox 同为装饰器模式。

worker 可以是 `AgentKernel`（同步执行 `worker.run(task)`，取 `RunState.status`/
`.answer`），也可以是任何实现 `Worker` 协议（`run(task) -> str`，失败直接抛异常）的
对象——比如 `AgentScopeWorker`（见 `adapters/tools/agentscope_worker.py`）。两条路径
在 `call()` 里分支处理。`AgentKernel` 路径还会传递委派深度，超过上限时在
`run()` 前失败；通用 Worker 保持原有协议，不要求暴露内核字段。
"""
from __future__ import annotations

from typing import Protocol, Union

from ...kernel import AgentKernel
from ...ports import ToolPort
from ...types import ToolResult, ToolSpec


class Worker(Protocol):
    """`AgentKernel` 之外的 worker 契约：`run(task)` 直接返回答案字符串，失败抛异常。

    `AgentKernel` 本身不实现这个 Protocol（它的 `run()` 返回 `RunState`），
    `WorkerDelegationPort` 对 `AgentKernel` 走专门分支保留原语义。
    """

    def run(self, task: str) -> str: ...


WorkerLike = Union[AgentKernel, Worker]


class DelegationDepthExceededError(RuntimeError):
    """委派链深度超过 max_delegation_depth（SPEC-88 Req4）。

    worker A 委派给 worker B、B 又委派回 A 时，depth 逐级递增；
    超过配置上限时在 worker.run() 之前抛出，防止无限递归。
    """

    def __init__(self, depth: int, max_depth: int | None, worker_name: str) -> None:
        self.depth = depth
        self.max_depth = max_depth
        self.worker_name = worker_name
        super().__init__(
            f"委派深度 {depth} 超过上限 {max_depth}（worker '{worker_name}'），"
            "已阻止递归委派"
        )


class WorkerDelegationPort(ToolPort):
    def __init__(self, inner: ToolPort) -> None:
        self._inner = inner
        self._workers: dict[str, WorkerLike] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, worker: WorkerLike, description: str = "") -> None:
        self._workers[name] = worker
        self._descriptions[name] = description or f"将任务委派给 worker '{name}'"

    def list_tools(self) -> list[ToolSpec]:
        worker_specs: list[ToolSpec] = []
        for name, desc in self._descriptions.items():
            tool_name = f"worker_{name}"
            worker_specs.append(
                ToolSpec(
                    name=tool_name,
                    description=desc,
                    parameters={
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": f"要委派给 {name} 的任务描述",
                            }
                        },
                        "required": ["task"],
                    },
                )
            )
        return [*self._inner.list_tools(), *worker_specs]

    def call(self, name: str, args: dict) -> ToolResult:
        if not name.startswith("worker_"):
            return self._inner.call(name, args)

        worker_name = name[len("worker_"):]
        if worker_name not in self._workers:
            raise KeyError(f"未知 worker: {worker_name}")

        task = str(args.get("task", ""))
        if not task.strip():
            raise ValueError("worker 任务不能为空")

        worker = self._workers[worker_name]

        if isinstance(worker, AgentKernel):
            previous_depth = worker.delegation_depth
            next_depth = previous_depth + 1
            max_depth = worker.max_delegation_depth
            if max_depth is not None and next_depth > max_depth:
                raise DelegationDepthExceededError(next_depth, max_depth, worker_name)
            worker.delegation_depth = next_depth
            try:
                state = worker.run(task)
            finally:
                worker.delegation_depth = previous_depth
            if state.status != "done":
                raise RuntimeError(
                    f"worker '{worker_name}' 执行失败，状态: {state.status}，答案: {state.answer}"
                )
            answer = state.answer
        else:
            answer = worker.run(task)

        if not answer:
            raise RuntimeError(f"worker '{worker_name}' 返回了空答案")

        return ToolResult(content=answer)
