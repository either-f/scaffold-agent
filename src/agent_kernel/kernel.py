"""微内核主循环：组装上下文 → 调模型 → 分发动作 → 更新状态。

设计要点：
- 只依赖 ports.py 的抽象，禁止第三方 import（CI 检查）。
- HITL：工具执行前询问 approval 回调（等价 LangGraph interrupt() 的最小实现）。
- 每步 checkpoint，支持断点恢复（M2 完成 --resume）。
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from typing import Callable

from .events import EventBus
from .ports import CheckpointStore, EffectLedger, MemoryPort, ModelPort, PlannerPort, ToolPort
from .schema import ToolArgumentValidationError, validate_arguments
from .types import (
    ArtifactRef,
    Effect,
    Event,
    FinalAnswer,
    RunState,
    ToolCall,
    ToolCallBatch,
    ToolEffectPolicy,
    ToolResult,
    hash_arguments,
)

Approval = Callable[[ToolCall], bool]

current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agent_kernel_current_run_id", default=None
)
"""SPEC-85: 当前 in-flight run 的 id。

ObservedModel（以及任何在 ModelPort 协议下拿不到 run_id 的观测包装器）读取本
ContextVar 给 model.complete 事件补 run_id，使 CostLedger / OTel 导出在多 run
共享同一 EventBus 时也能正确按 run 归因。

由 AgentKernel.run()/resume()/_drive() 在 run 生命期内设置；不并发时零开销
（一次 set + 一次 reset）。零依赖、纯标准库。
"""


class RunFailedError(RuntimeError):
    """planner/model 抛异常导致 run 终结。checkpoint 已落盘，原始异常挂在 __cause__ 上，
    供想看 traceback 的直接调用方取用；只检查 RunState 的调用方则看到 status="failed"。"""

    def __init__(self, run_id: str, reason: str, cause: BaseException) -> None:
        self.run_id = run_id
        self.reason = reason
        self.__cause__ = cause
        super().__init__(f"run {run_id} 失败（{reason}）: {type(cause).__name__}")


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
    """账本里 effect_id 命中的记录，参数哈希 / tool_name / run_id 跟本次调用对不上：
    说明 effect_id 发生了冲突（例如跨轮复用了同一个 id，或 fork 后错误地复用了源 run 的行），
    账本已不可信。拒绝自动回放/重试，需要人工核实后再处理。"""

    def __init__(self, effect_id: str, run_id: str, tool_name: str) -> None:
        self.effect_id = effect_id
        self.run_id = run_id
        self.tool_name = tool_name
        super().__init__(
            f"effect {effect_id}（工具 {tool_name}）账本记录与本次调用不一致"
            "（参数哈希/tool_name/run_id 不匹配），疑似 effect_id 冲突，"
            "拒绝自动回放/重试，需要人工核实"
        )


class ApprovalRequiredError(RuntimeError):
    """工具通过 ToolEffectPolicy.requires_approval=True 声明需要审批，
    但内核未配置 approval 回调。fail-closed：执行前直接抛出，
    不静默跑未审批的副作用工具。"""

    def __init__(self, run_id: str, tool_name: str) -> None:
        self.run_id = run_id
        self.tool_name = tool_name
        super().__init__(
            f"工具 {tool_name} 声明 requires_approval=True，但内核未配置 approval 回调；"
            "fail-closed 拒绝执行（构造 AgentKernel 时传 approval=回调 以放行）"
        )


class AgentKernel:
    """微内核主循环。

    取消与截止（SPEC-88 Req1/Req2）：
    - ``cancel``: 协作式取消回调，每步循环顶部检查。由于 ``ModelPort.complete`` /
      ``ToolPort.call`` 是同步阻塞调用，取消只能在步骤之间生效（cooperative），
      不能抢占一个正在执行的 model/tool 调用——那需要线程/子进程隔离，显式超出范围。
    - ``timeout_seconds``: 每个 ``run()`` 的墙上时钟上限，同样在步骤之间检查。
    取消或超时后状态转为 ``"cancelled"``，checkpoint 干净，可被检查。
    """

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
        *,
        cancel: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        delegation_depth: int = 0,
        max_delegation_depth: int | None = None,
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
        # SPEC-88 Req1: 协作式取消回调。每次 _drive 循环顶部调用，返回 True 时终止。
        self.cancel = cancel
        # SPEC-88 Req2: 每次 run() 的墙上时钟截止（秒），转成 monotonic 绝对时间戳。
        self.timeout_seconds = timeout_seconds
        self._run_deadline: float | None = None
        # SPEC-88 Req4: 委派深度上下文，WorkerDelegationPort 在 call() 里读取并传递。
        self.delegation_depth = delegation_depth
        self.max_delegation_depth = max_delegation_depth
        # SPEC-94: per-run_id 单调递增事件序列号。两个并发 run 各自从 1 开始。
        self._event_sequences: dict[str, int] = {}
        self._event_sequence_lock = threading.Lock()

    # ------------------------------------------------------------------ utils
    def _emit(self, type_: str, **payload) -> None:
        # SPEC-94: 统一盖戳 event_id（uuid4 hex，保证唯一）与 sequence（按 run_id 递增），
        # 调用方继续只传 type_ 和 **payload，无需逐处改写。run_id 缺失时退化为单进程全局序列。
        run_id = str(payload.get("run_id", "")) if payload else ""
        with self._event_sequence_lock:
            seq = self._event_sequences.get(run_id, 0) + 1
            self._event_sequences[run_id] = seq
        event = Event(type=type_, payload=payload, sequence=seq)
        self.bus.publish(event)

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
            state.last_error = None
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
        # SPEC-88 Req2: run() 入口设置 deadline（monotonic 绝对时间戳）。
        self._run_deadline = (
            time.monotonic() + self.timeout_seconds
            if self.timeout_seconds is not None
            else None
        )
        return self._with_run_context(state, self._drive, state)

    def resume(self, state: RunState) -> RunState:
        if state.status in {"done", "failed", "cancelled"}:
            raise ValueError(f"终态 run 不能恢复: {state.status}")
        if state.status not in {"running", "paused"}:
            raise ValueError(f"未知 run 状态: {state.status}")
        if state.status == "paused" and state.pending_tool is None:
            raise ValueError("paused checkpoint 缺少 pending_tool")
        if state.status == "paused" and self.approval is None:
            raise PermissionError("恢复待审批工具必须提供 approval")
        # turn==0 兼容旧 checkpoint 的逻辑已集中到 RunState.from_dict（按 schema_version
        # 分支），此处不再重复。见 SPEC-86。
        self._emit("run.resume", run_id=state.run_id, step=state.step, status=state.status)
        self._emit("run.resumed", run_id=state.run_id, step=state.step, status=state.status)
        # SPEC-88 Req2: resume() 入口同样设置 deadline。
        self._run_deadline = (
            time.monotonic() + self.timeout_seconds
            if self.timeout_seconds is not None
            else None
        )
        return self._with_run_context(state, self._drive, state)

    def _with_run_context(self, state: RunState, fn: Callable[..., RunState], *args) -> RunState:
        """SPEC-85: 在 fn 执行期间把 state.run_id 写入 current_run_id ContextVar，
        使 ObservedModel 等拿不到 run_id 的观测包装器也能正确归因 model.complete。
        用 reset() 保证并发 run 互不污染；不并发时零开销。"""
        token = current_run_id.set(state.run_id)
        try:
            return fn(*args)
        finally:
            current_run_id.reset(token)

    def _drive(self, state: RunState) -> RunState:
        while state.status in {"running", "paused"}:
            # SPEC-88 Req1/Req2: 协作式取消与截止检查，在每个循环迭代顶部生效。
            if self._check_cancel_or_deadline(state):
                break
            if state.pending_tool is not None:
                self._run_pending_tool(state)
                # SPEC-88 Req1: 工具执行完也是取消检查点（"工具结果返回后，再决定继续"）。
                if self._check_cancel_or_deadline(state):
                    break
                continue
            if state.step >= self.max_steps:
                state.status = "failed"
                state.answer = "已达最大步数限制。"
                state.last_error = state.answer
                self._emit(
                    "run.failed",
                    run_id=state.run_id,
                    step=state.step,
                    answer=state.answer,
                    reason="max_steps",
                    error_type="MaxStepsExhausted",
                    error_message=state.answer,
                )
                self._checkpoint(state)
                break
            state.step += 1
            self._emit("step.start", run_id=state.run_id, step=state.step)

            old_summary = state.context_summary
            old_summary_count = state.summarized_message_count
            try:
                action = self.planner.step(state, self.model, self.tools, self.memory)
            except Exception as exc:
                self._emit_context_summary_if_changed(
                    state, old_summary, old_summary_count
                )
                state.status = "failed"
                state.answer = f"运行失败: {type(exc).__name__}（planner/model 调用异常）。"
                state.last_error = state.answer
                self._emit(
                    "run.failed",
                    run_id=state.run_id,
                    step=state.step,
                    answer=state.answer,
                    reason="planner_exception",
                    error_type=type(exc).__name__,
                    error_message=state.answer,
                )
                self._checkpoint(state)
                raise RunFailedError(state.run_id, "planner_exception", exc) from exc
            self._emit_context_summary_if_changed(state, old_summary, old_summary_count)
            # 同步 model 调用可能跨过 deadline；返回后、派发工具或接受 final 前再检查一次。
            if self._check_cancel_or_deadline(state):
                break

            if isinstance(action, FinalAnswer):
                state.answer = action.content
                state.status = "done"
                state.add("assistant", action.content)
                if self.memory:
                    self.memory.add(state.run_id, "assistant", action.content)
                self._emit("run.completed", run_id=state.run_id, step=state.step, answer=action.content)
            elif isinstance(action, ToolCallBatch):
                # SPEC-88 Req3: 并行工具调用批次，顺序执行每个工具调用。
                for tc in action.calls:
                    self._validate_tool_call(state, tc)
                    self._emit("tool.before", run_id=state.run_id, tool=tc.name, args=tc.args)
                    if self.approval is None:
                        policy = self._effect_policy(tc.name)
                        if policy is not None and policy.requires_approval:
                            raise ApprovalRequiredError(state.run_id, tc.name)
                # 批次内第一个工具走 pending_tool 路径，其余通过 _execute_batch_tool 顺序执行。
                # 逐个执行：每个工具走 propose -> approve(可选) -> execute -> finish 完整流程，
                # 但复用 pending_tool 单步机制需要逐步 checkpoint。简化实现：直接顺序执行全部。
                self._execute_batch(state, action)
            elif isinstance(action, ToolCall):
                self._validate_tool_call(state, action)
                self._emit("tool.before", run_id=state.run_id, tool=action.name, args=action.args)
                # fail-closed：工具声明 requires_approval=True 但内核没有 approval 回调，
                # 拒绝执行副作用（ADR-0008 的审批契约由工具自己声明，而非仅由调用方决定）。
                if self.approval is None:
                    policy = self._effect_policy(action.name)
                    if policy is not None and policy.requires_approval:
                        raise ApprovalRequiredError(state.run_id, action.name)
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

    def _emit_context_summary_if_changed(
        self,
        state: RunState,
        old_summary: str,
        old_summary_count: int,
    ) -> None:
        if (
            state.context_summary != old_summary
            or state.summarized_message_count != old_summary_count
        ):
            self._emit(
                "context.summarized",
                run_id=state.run_id,
                context_summary=state.context_summary,
                summarized_message_count=state.summarized_message_count,
            )

    def _validate_tool_call(self, state: RunState, action: ToolCall) -> None:
        """Validate the repository's small JSON-Schema subset before effects/approval."""
        tool_spec = next((t for t in self.tools.list_tools() if t.name == action.name), None)
        if tool_spec is None:
            return
        try:
            validate_arguments(action.name, tool_spec.parameters, action.args)
        except ToolArgumentValidationError as exc:
            state.status = "failed"
            state.answer = f"运行失败: {exc}"
            state.last_error = state.answer
            self._emit(
                "run.failed",
                run_id=state.run_id,
                step=state.step,
                answer=state.answer,
                reason="tool_argument_validation",
                error_type=type(exc).__name__,
                error_message=state.answer,
            )
            self._checkpoint(state)
            raise

    def _check_cancel_or_deadline(self, state: RunState) -> bool:
        """SPEC-88 Req1/Req2: 检查取消回调与截止时间。

        返回 True 表示已触发终止（状态已转为 ``"cancelled"``，checkpoint 已保存），
        调用方应 break 循环。取消是协作式的--只在步骤之间检查，不能抢占一个
        正在阻塞的 ``ModelPort.complete`` / ``ToolPort.call`` 调用。
        """
        cancelled = False
        reason = ""
        if self.cancel is not None and self.cancel():
            cancelled = True
            reason = "cancel"
        elif self._run_deadline is not None and time.monotonic() >= self._run_deadline:
            cancelled = True
            reason = "timeout"
        if cancelled:
            state.status = "cancelled"
            state.answer = state.answer or f"run 已{'取消' if reason == 'cancel' else '超时'}。"
            self._emit(
                "run.cancelled",
                run_id=state.run_id,
                step=state.step,
                reason=reason,
                answer=state.answer,
            )
            self._checkpoint(state)
            return True
        return False

    def _execute_batch(self, state: RunState, batch: ToolCallBatch) -> None:
        """SPEC-88 Req3: 顺序执行一个 ToolCallBatch 里的所有工具调用。

        选择顺序执行（而非真正并发）以保持与 Effect Ledger 交互的简单性--
        每个 call 独立 propose -> (approve) -> execute -> finish，互不干扰。
        approval 路径下批次中任意一个被否决仍继续执行剩余工具（结果为否决提示），
        与单工具 HITL 语义一致。
        """
        for index, tc in enumerate(batch.calls):
            state.pending_tool = tc
            state.pending_effect_id = None
            effect_id = None
            if self.effects:
                effect_id = f"{state.run_id}:{state.turn}:{state.step}:{index}"
                state.pending_effect_id = effect_id
                self.effects.propose(
                    Effect(
                        effect_id,
                        state.run_id,
                        tc.name,
                        hash_arguments(tc.args),
                        idempotency_key=f"effect:{effect_id}",
                    )
                )
            self._emit(
                "tool.proposed",
                run_id=state.run_id,
                step=state.step,
                tool=tc.name,
                args=tc.args,
                thought=tc.thought,
                effect_id=effect_id,
            )
            # 批次是同一个 planner step，无法用单一 pending_tool 跨 resume 保存剩余调用；
            # 有同步 approval 回调时逐项审批并立即执行。
            if self.approval is not None:
                state.status = "paused"
            self._run_pending_tool(state)
            # _run_pending_tool 会把 pending_tool 清空；下个 call 重新设置。
            if self._check_cancel_or_deadline(state):
                break

    def _run_pending_tool(self, state: RunState) -> None:
        action = state.pending_tool
        assert action is not None
        self._validate_tool_call(state, action)

        # fork-safe effect identity（SPEC-80 Req1）：fork() 会清掉 pending_effect_id，
        # 让分支上第一次 resume 走到这里时，用分支自己的 run_id 重新 propose 一行，
        # 而不是复用源 run 的 effect 行（那会让两个分支共享一行、互相串结果）。
        if self.effects and state.pending_effect_id is None:
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

        effect = (
            self.effects.get(state.pending_effect_id)
            if self.effects and state.pending_effect_id
            else None
        )
        # 校验账本命中行确实是"本次调用"的行：参数哈希 + tool_name + run_id 三者都要对上。
        # 单独只校验 hash 会被零参工具碰撞（hash_arguments({}) 相同）绕过。
        if effect is not None and effect.arguments_hash != hash_arguments(action.args):
            raise EffectArgumentMismatchError(effect.effect_id, state.run_id, action.name)
        if effect is not None and effect.tool_name != action.name:
            raise EffectArgumentMismatchError(effect.effect_id, state.run_id, action.name)
        if effect is not None and effect.run_id != state.run_id:
            raise EffectArgumentMismatchError(effect.effect_id, state.run_id, action.name)
        # 终态行（含本 Spec 新增的 rejected）不允许自动重放/重试。
        if effect is not None and effect.status in {"succeeded", "executing", "failed", "rejected"}:
            if effect.status == "succeeded":
                self._emit(
                    "effect.replay", run_id=state.run_id, effect_id=effect.effect_id, tool=action.name
                )
                # 回放时重建 ToolResult：优先解码 result_ref 里的 JSON（带 artifacts），
                # 兼容纯文本旧行（SPEC-80 Req7）。
                self._finish_tool(state, action, _decode_replay_result(effect.result_ref))
                return
            if effect.status == "rejected":
                # rejected 是终态：人工/审批已明确拒绝，内核不自动重跑。
                # 走到这里的唯一可能是同一 effect_id 被复用到一个新的、合法的调用上——
                # 但那会先被上面的 tool_name/run_id/hash 校验拦下。这里保守拒绝重放。
                raise EffectUnresolvedError(
                    effect.effect_id, state.run_id, action.name, effect.status
                )
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
                # SPEC-80 Req4：被否决的工具调用要把 effect 行落到终态 "rejected"，
                # 不能一直停在 "proposed" 造成死胡同。
                if self.effects and state.pending_effect_id:
                    self.effects.mark_rejected(state.pending_effect_id)
                self._finish_tool(
                    state, action, ToolResult(content=f"[HITL] 用户否决了工具调用 {action.name}。")
                )
                return
            if self.effects and state.pending_effect_id:
                self.effects.mark_approved(state.pending_effect_id)
            self._checkpoint(state)  # 持久化批准；恢复后不重复询问
            if self._check_cancel_or_deadline(state):
                return

        result = self._execute_tool(state, action)
        self._finish_tool(state, action, result)

    def _execute_tool(self, state: RunState, action: ToolCall) -> ToolResult:
        self._emit("tool.started", run_id=state.run_id, step=state.step, tool=action.name, args=action.args)
        if self.effects and state.pending_effect_id:
            self.effects.mark_executing(state.pending_effect_id)
        try:
            result = self.tools.call(action.name, action.args)
        except Exception as exc:  # 工具失败也要回灌上下文，让模型自己纠错
            result = ToolResult(content=f"[tool-error] {exc}", is_error=True)
            if self.effects and state.pending_effect_id:
                self.effects.mark_failed(state.pending_effect_id, result.content)
            return result
        if self.effects and state.pending_effect_id and result.is_error:
            self.effects.mark_failed(state.pending_effect_id, result.content)
        elif self.effects and state.pending_effect_id:
            # SPEC-80 Req7：成功时把 content + artifacts 一起 JSON 编码进 result_ref，
            # 这样崩溃恢复后回放能重建带 ArtifactRef 的 ToolResult，而不是只剩扁平文本。
            self.effects.mark_succeeded(
                state.pending_effect_id, _encode_success_result_ref(result)
            )
        return result

    def _effect_policy(self, tool_name: str) -> ToolEffectPolicy | None:
        return next((t.effect_policy for t in self.tools.list_tools() if t.name == tool_name), None)

    def _finish_tool(self, state: RunState, action: ToolCall, result: ToolResult) -> None:
        # effect ledger 的 result_ref 在 _execute_tool 里已落 JSON envelope（content+artifacts），
        # 回放分支由 _decode_replay_result 解码重建；这里只负责消息历史与事件流。
        text = result.content
        if result.artifacts:
            text += "\n" + "\n".join(f"[artifact] {a.uri} ({a.mime_type})" for a in result.artifacts)
        artifacts_payload = [
            {"uri": a.uri, "mime_type": a.mime_type, "description": a.description} for a in result.artifacts
        ]
        self._emit(
            "tool.after",
            run_id=state.run_id,
            tool=action.name,
            result=text,
            artifacts=artifacts_payload,
            is_error=result.is_error,
            structured_content=result.structured_content,
        )
        if action.call_id is not None:
            # 原生 tool calling 回路：assistant 消息带 tool_calls，紧接的 tool 消息带
            # 同一 tool_call_id，供 LiteLLMModel.complete 还原 provider 协议要求的形状。
            state.add(
                "assistant",
                action.thought,
                tool_calls=[
                    {"id": action.call_id, "name": action.name, "args": action.args}
                ],
            )
            state.add("tool", text, name=action.name, tool_call_id=action.call_id)
            assistant_message = json.dumps(
                {"thought": action.thought, "tool": action.name, "args": action.args},
                ensure_ascii=False,
            )
        else:
            # 非 native 路径（文本 JSON / FakeScriptedModel）：保持旧的扁平化行为，
            # 离线 eval 字节不变。
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
            is_error=result.is_error,
            structured_content=result.structured_content,
        )
        self._checkpoint(state)


# --------------------------------------------------------------------------- #
# result_ref 编解码（SPEC-80 Req7：回放要能重建带 ArtifactRef 的 ToolResult）
# --------------------------------------------------------------------------- #
# 成功行：JSON envelope {"content": "...", "artifacts": [{"uri","mime_type","description"}]}
# 失败行：保留纯文本（mark_failed 只存错误文本，不需要 artifacts）
# 旧行兼容：decode 时 JSON 解析失败就退回 ToolResult(content=原文本)。

def _encode_success_result_ref(result: ToolResult) -> str:
    """把 ToolResult 序列化成可塞进 effect.result_ref 的字符串。
    带 artifacts 时用 JSON envelope；无 artifacts 且 content 是合法 JSON 时
    仍走 envelope（key 固定），保证 decode 路径单一。"""
    payload = {
        "content": result.content,
        "artifacts": [
            {"uri": a.uri, "mime_type": a.mime_type, "description": a.description}
            for a in result.artifacts
        ],
        "is_error": result.is_error,
        "structured_content": result.structured_content,
    }
    return json.dumps(payload, ensure_ascii=False)


def _decode_replay_result(result_ref: str | None) -> ToolResult:
    """从 effect.result_ref 重建 ToolResult。
    - JSON envelope（本 Spec 之后写的行）：还原 content + artifacts。
    - 非法 JSON / 缺字段（旧行、纯文本行）：退回 ToolResult(content=原文)，无 artifacts。"""
    if not result_ref:
        return ToolResult(content="")
    try:
        payload = json.loads(result_ref)
    except (json.JSONDecodeError, TypeError):
        return ToolResult(content=result_ref)
    if not isinstance(payload, dict) or "content" not in payload:
        return ToolResult(content=result_ref)
    raw_artifacts = payload.get("artifacts") or []
    artifacts: list[ArtifactRef] = []
    for a in raw_artifacts:
        if not isinstance(a, dict) or "uri" not in a:
            continue
        artifacts.append(
            ArtifactRef(
                uri=str(a["uri"]),
                mime_type=str(a.get("mime_type", "text/plain")),
                description=str(a.get("description", "")),
            )
        )
    return ToolResult(
        content=str(payload["content"]),
        artifacts=artifacts,
        is_error=bool(payload.get("is_error", False)),
        structured_content=payload.get("structured_content"),
    )

