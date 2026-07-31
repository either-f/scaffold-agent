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
from .ports import CheckpointStore, EffectLedger, MemoryPort, ModelPort, PlannerPort, ToolPort
from .types import (
    Effect,
    Event,
    FinalAnswer,
    RunState,
    ToolCall,
    ToolEffectPolicy,
    ToolResult,
    hash_arguments,
)

Approval = Callable[[ToolCall], bool]


class EffectUnresolvedError(RuntimeError):
    """恢复时发现 effect 停在 executing/failed，且工具非幂等（或已超重试上限）：
    内核拒绝自动重试，需要人工核实外部系统真实状态后手动改账本再 resume()。"""

    def __init__(self, effect_id: str, run_id: str, tool_name: str, status: str) -> None:
        self.effect_id = effect_id
        self.run_id = run_id
        self.tool_name = tool_name
        self.status = status
        super().__init__(
            f"effect {effect_id}（工具 {tool_name}）状态为 {status}，不允许自动重试，"
            "需要人工核实外部系统后手动修正账本（mark_succeeded/mark_failed）再 resume()"
        )


class EffectArgumentMismatchError(RuntimeError):
    """账本里 effect_id 命中的记录，参数哈希却跟本次调用对不上：
    说明 effect_id 发生了冲突（例如跨轮复用了同一个 id），账本已不可信。
    拒绝自动回放/重试，需要人工核实后再处理。"""

    def __init__(self, effect_id: str, run_id: str, tool_name: str) -> None:
        self.effect_id = effect_id
        self.run_id = run_id
        self.tool_name = tool_name
        super().__init__(
            f"effect {effect_id}（工具 {tool_name}）账本参数哈希与本次调用不一致，"
            "疑似 effect_id 冲突，拒绝自动回放/重试，需要人工核实"
        )


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
        effects: EffectLedger | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.bus = bus or EventBus()
        self.checkpoints = checkpoints
        self.approval = approval
        self.max_steps = max_steps
        self.effects = effects

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
        self._emit("run.started", run_id=state.run_id, turn=state.turn, input=user_input)
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
        self._emit("run.resumed", run_id=state.run_id, step=state.step, status=state.status)
        return self._drive(state)

    def _drive(self, state: RunState) -> RunState:
        while state.status in {"running", "paused"}:
            if state.pending_tool is not None:
                self._run_pending_tool(state)
                continue
            if state.step >= self.max_steps:
                state.status = "failed"
                state.answer = "已达最大步数限制。"
                self._emit("run.failed", run_id=state.run_id, step=state.step, answer=state.answer)
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
                self._emit("run.completed", run_id=state.run_id, step=state.step, answer=action.content)
            elif isinstance(action, ToolCall):
                self._emit("tool.before", run_id=state.run_id, tool=action.name, args=action.args)
                state.pending_tool = action
                effect_id = None
                if self.effects:
                    effect_id = f"{state.run_id}:{state.turn}:{state.step}"
                    state.pending_effect_id = effect_id
                    self.effects.propose(
                        Effect(
                            effect_id,
                            state.run_id,
                            action.name,
                            hash_arguments(action.args),
                            idempotency_key=f"effect:{effect_id}",
                        )
                    )
                self._emit(
                    "tool.proposed",
                    run_id=state.run_id,
                    step=state.step,
                    tool=action.name,
                    args=action.args,
                    thought=action.thought,
                    effect_id=effect_id,
                )
                state.status = "paused" if self.approval else "running"
                if state.status == "paused":
                    self._emit("run.paused", run_id=state.run_id, step=state.step)

            self._checkpoint(state)

        self._emit("run.end", run_id=state.run_id, status=state.status, answer=state.answer)
        return state

    def _run_pending_tool(self, state: RunState) -> None:
        action = state.pending_tool
        assert action is not None

        effect = (
            self.effects.get(state.pending_effect_id)
            if self.effects and state.pending_effect_id
            else None
        )
        if effect is not None and effect.arguments_hash != hash_arguments(action.args):
            raise EffectArgumentMismatchError(effect.effect_id, state.run_id, action.name)
        if effect is not None and effect.status in {"succeeded", "executing", "failed"}:
            if effect.status == "succeeded":
                self._emit(
                    "effect.replay", run_id=state.run_id, effect_id=effect.effect_id, tool=action.name
                )
                self._finish_tool(state, action, ToolResult(content=effect.result_ref or ""))
                return
            policy = self._effect_policy(action.name)
            max_attempts = policy.retry_policy.max_attempts if policy else 1
            if not (policy and policy.idempotent and effect.attempt < max_attempts):
                raise EffectUnresolvedError(effect.effect_id, state.run_id, action.name, effect.status)
            self._emit(
                "effect.retry",
                run_id=state.run_id,
                effect_id=effect.effect_id,
                tool=action.name,
                from_status=effect.status,
            )

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
            self._emit(
                "tool.approved", run_id=state.run_id, step=state.step, tool=action.name, approved=approved
            )
            state.status = "running"
            if not approved:
                self._finish_tool(
                    state, action, ToolResult(content=f"[HITL] 用户否决了工具调用 {action.name}。")
                )
                return
            if self.effects and state.pending_effect_id:
                self.effects.mark_approved(state.pending_effect_id)
            self._checkpoint(state)  # 持久化批准；恢复后不重复询问

        result = self._execute_tool(state, action)
        self._finish_tool(state, action, result)

    def _execute_tool(self, state: RunState, action: ToolCall) -> ToolResult:
        self._emit("tool.started", run_id=state.run_id, step=state.step, tool=action.name, args=action.args)
        if self.effects and state.pending_effect_id:
            self.effects.mark_executing(state.pending_effect_id)
        try:
            result = self.tools.call(action.name, action.args)
        except Exception as exc:  # 工具失败也要回灌上下文，让模型自己纠错
            result = ToolResult(content=f"[tool-error] {exc}")
            if self.effects and state.pending_effect_id:
                self.effects.mark_failed(state.pending_effect_id, result.content)
            return result
        if self.effects and state.pending_effect_id:
            self.effects.mark_succeeded(state.pending_effect_id, result.content)
        return result

    def _effect_policy(self, tool_name: str) -> ToolEffectPolicy | None:
        return next((t.effect_policy for t in self.tools.list_tools() if t.name == tool_name), None)

    def _finish_tool(self, state: RunState, action: ToolCall, result: ToolResult) -> None:
        # ponytail: effect ledger 与消息历史都只落 text；artifacts 只在事件流里给外部观测器，
        # 副作用重放不恢复 artifacts（回放场景下模型只需要看到当时回灌的文本即可）。
        text = result.content
        if result.artifacts:
            text += "\n" + "\n".join(f"[artifact] {a.uri} ({a.mime_type})" for a in result.artifacts)
        artifacts_payload = [
            {"uri": a.uri, "mime_type": a.mime_type, "description": a.description} for a in result.artifacts
        ]
        self._emit("tool.after", run_id=state.run_id, tool=action.name, result=text, artifacts=artifacts_payload)
        assistant_message = json.dumps(
            {"thought": action.thought, "tool": action.name, "args": action.args},
            ensure_ascii=False,
        )
        state.add("assistant", assistant_message)
        state.add("tool", text, name=action.name)
        if self.memory:
            self.memory.add(state.run_id, "tool", f"{action.name}: {text}")
        state.pending_tool = None
        state.pending_effect_id = None
        self._emit(
            "tool.completed",
            run_id=state.run_id,
            step=state.step,
            tool=action.name,
            args=action.args,
            result=text,
            artifacts=artifacts_payload,
            assistant_message=assistant_message,
        )
        self._checkpoint(state)
