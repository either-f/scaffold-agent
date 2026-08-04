"""worker 委派工具箱：ToolPort 装饰器，把 AgentKernel 暴露为带前缀的工具。

每个注册的 worker 暴露为一个 `worker_<name>` 工具，接受 `task` 字符串参数。
调用时同步执行 worker.kernel.run(task)，返回最终答案；失败或空答案抛出 RuntimeError。
内层 ToolPort 的工具照常透传，与 SkillToolbox 同为装饰器模式。
"""
from __future__ import annotations

from ...kernel import AgentKernel
from ...ports import ToolPort
from ...types import ToolResult, ToolSpec


class WorkerDelegationPort(ToolPort):
    def __init__(self, inner: ToolPort) -> None:
        self._inner = inner
        self._workers: dict[str, AgentKernel] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, worker: AgentKernel, description: str = "") -> None:
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
        state = worker.run(task)

        if state.status != "done":
            raise RuntimeError(
                f"worker '{worker_name}' 执行失败，状态: {state.status}，答案: {state.answer}"
            )

        answer = state.answer
        if not answer:
            raise RuntimeError(f"worker '{worker_name}' 返回了空答案")

        return ToolResult(content=answer)
