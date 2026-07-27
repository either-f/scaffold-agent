"""微内核主循环：组装上下文 → 调模型 → 分发动作 → 更新状态。

设计要点：
- 只依赖 ports.py 的抽象，禁止第三方 import（CI 检查）。
- HITL：工具执行前询问 approval 回调（等价 LangGraph interrupt() 的最小实现）。
- 每步 checkpoint，支持断点恢复（M2 完成 --resume）。
"""
from __future__ import annotations

import json
from typing import Callable

from .events import EventBus
from .ports import CheckpointStore, MemoryPort, ModelPort, PlannerPort, ToolPort
from .types import Event, FinalAnswer, RunState, ToolCall

Approval = Callable[[ToolCall], bool]


class AgentKernel:
    def __init__(
        self,
        model: ModelPort,
        tools: ToolPort,
        planner: PlannerPort,
        memory: MemoryPort | None = None,
        bus: EventBus | None = None,
        checkpoints: CheckpointStore | None = None,
        approval: Approval | None = None,
        max_steps: int = 10,
    ) -> None:
        self.model = model
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.bus = bus or EventBus()
        self.checkpoints = checkpoints
        self.approval = approval
        self.max_steps = max_steps

    # ------------------------------------------------------------------ utils
    def _emit(self, type_: str, **payload) -> None:
        self.bus.publish(Event(type_, payload))

    def _checkpoint(self, state: RunState) -> None:
        if self.checkpoints:
            self.checkpoints.save(state)

    # ------------------------------------------------------------------- run
    def run(self, user_input: str, state: RunState | None = None) -> RunState:
        state = state or RunState()
        if state.status == "done":
            state.turn = max(state.turn, 1) + 1
            state.step = 0
            state.status = "running"
            state.answer = None
            state.pending_tool = None
        elif state.status in {"paused", "failed"}:
            raise ValueError(f"{state.status} run 不能直接续聊")
        elif state.status != "running":
            raise ValueError(f"未知 run 状态: {state.status}")
        elif state.messages:
            raise ValueError("running run 不能追加新输入，请使用 resume()")
        else:
            state.turn = 1
        state.add("user", user_input)
        if self.memory:
            self.memory.add(state.run_id, "user", user_input)
        self._emit("run.start", run_id=state.run_id, input=user_input)
        self._checkpoint(state)
        return self._drive(state)

    def resume(self, state: RunState) -> RunState:
        if state.status in {"done", "failed"}:
            raise ValueError(f"终态 run 不能恢复: {state.status}")
        if state.status not in {"running", "paused"}:
            raise ValueError(f"未知 run 状态: {state.status}")
        if state.status == "paused" and state.pending_tool is None:
            raise ValueError("paused checkpoint 缺少 pending_tool")
        if state.status == "paused" and self.approval is None:
            raise PermissionError("恢复待审批工具必须提供 approval")
        if state.turn == 0:  # 兼容 M2 checkpoint
            state.turn = 1
        self._emit("run.resume", run_id=state.run_id, step=state.step, status=state.status)
        return self._drive(state)

    def _drive(self, state: RunState) -> RunState:
        while state.status in {"running", "paused"}:
            if state.pending_tool is not None:
                self._run_pending_tool(state)
                continue
            if state.step >= self.max_steps:
                state.status = "failed"
                state.answer = "已达最大步数限制。"
                self._checkpoint(state)
                break
            state.step += 1
            self._emit("step.start", run_id=state.run_id, step=state.step)

            action = self.planner.step(state, self.model, self.tools, self.memory)

            if isinstance(action, FinalAnswer):
                state.answer = action.content
                state.status = "done"
                state.add("assistant", action.content)
                if self.memory:
                    self.memory.add(state.run_id, "assistant", action.content)
            elif isinstance(action, ToolCall):
                self._emit("tool.before", run_id=state.run_id, tool=action.name, args=action.args)
                state.pending_tool = action
                state.status = "paused" if self.approval else "running"

            self._checkpoint(state)

        self._emit("run.end", run_id=state.run_id, status=state.status, answer=state.answer)
        return state

    def _run_pending_tool(self, state: RunState) -> None:
        action = state.pending_tool
        assert action is not None

        if state.status == "paused":
            assert self.approval is not None  # resume() 与创建 pending 时已保证
            approved = self.approval(action)
            self._emit(
                "tool.approval",
                run_id=state.run_id,
                tool=action.name,
                args=action.args,
                approved=approved,
            )
            state.status = "running"
            if not approved:
                self._finish_tool(state, action, f"[HITL] 用户否决了工具调用 {action.name}。")
                return
            self._checkpoint(state)  # 持久化批准；恢复后不重复询问

        try:
            result = self.tools.call(action.name, action.args)
        except Exception as exc:  # 工具失败也要回灌上下文，让模型自己纠错
            result = f"[tool-error] {exc}"
        self._finish_tool(state, action, result)

    def _finish_tool(self, state: RunState, action: ToolCall, result: str) -> None:
        self._emit("tool.after", run_id=state.run_id, tool=action.name, result=result)
        state.add(
            "assistant",
            json.dumps(
                {"thought": action.thought, "tool": action.name, "args": action.args},
                ensure_ascii=False,
            ),
        )
        state.add("tool", result, name=action.name)
        if self.memory:
            self.memory.add(state.run_id, "tool", f"{action.name}: {result}")
        state.pending_tool = None
        self._checkpoint(state)
