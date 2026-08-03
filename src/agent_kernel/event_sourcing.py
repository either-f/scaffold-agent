"""从事件重建 RunState（ADR-0009）。纯标准库，只依赖 .types。

SPEC-94 后覆盖范围：
- run.started / run.forked → run_id / turn / step / forked_from
- tool.proposed / tool.completed → step / pending_tool / 消息历史（含 artifacts）
- context.summarized → context_summary / summarized_message_count
- run.failed → answer / last_error / error_type / error_message
- run.completed / run.paused / run.resumed → status / answer

已知残留边界（SPEC-94 后）：
- ``ts`` 字段重建时取事件自身时间戳，不回写到 ``RunState``（RunState 无该字段）。
- 副作用账本（``Effect`` 表）独立于事件流，``reduce`` 不重建 effect 状态；
  恢复场景请走 ``AgentKernel.resume(checkpoint)`` 而非纯事件重建。
- ``effect.replay`` / ``effect.retry`` 事件仅审计用途，``reduce`` 不据此改写
  ``ToolResult.content``（重放场景下模型只需看到当时回灌的文本）。
"""
from __future__ import annotations

from .types import Event, RunState, ToolCall


def reduce(events: list[Event], initial: RunState | None = None) -> RunState:
    state = initial or RunState()
    for ev in events:
        p = ev.payload
        if ev.type == "run.started":
            state.run_id = p["run_id"]
            state.turn = p["turn"]
            state.step = 0
            state.status = "running"
            state.answer = None
            state.last_error = None
            state.pending_tool = None
            state.pending_effect_id = None
            state.add("user", p["input"])
        elif ev.type == "run.forked":
            # SPEC-94: fork 出来的 run 不走 run.started，用 run.forked 重建 run_id /
            # forked_from / turn / step。turn/step 缺失时退化为 0，向前兼容旧 fork 事件。
            state.run_id = p["run_id"]
            state.forked_from = p.get("forked_from")
            state.turn = p.get("turn", state.turn)
            state.step = p.get("step", state.step)
            state.status = "running"
        elif ev.type == "tool.proposed":
            state.step = p["step"]
            state.pending_tool = ToolCall(name=p["tool"], args=p["args"], thought=p.get("thought", ""))
            state.pending_effect_id = p.get("effect_id")
            state.status = "running"
        elif ev.type == "run.paused":
            state.status = "paused"
        elif ev.type == "tool.approved":
            state.status = "running"
        elif ev.type == "tool.completed":
            state.add("assistant", p["assistant_message"])
            # SPEC-94: tool.completed 现在带 artifacts，重建到 ToolResult/消息历史。
            # 消息历史里 tool 消息存的是 text（含 [artifact] 行），跟 kernel._finish_tool 一致；
            # artifacts 单独挂到 pending_tool 旁的结构化引用上无对应字段，故只确保
            # text 一致（artifacts 已内联进 text）。reduce 不单独重建 ArtifactRef 列表
            # 到 RunState（RunState 无该字段），但事件流已完整保留 artifacts_payload。
            state.add("tool", p["result"], name=p["tool"])
            state.pending_tool = None
            state.pending_effect_id = None
        elif ev.type == "context.summarized":
            # SPEC-94: ContextBuilder 摘要落定后发此事件，reduce 据此重建 context_summary。
            state.context_summary = p.get("context_summary", "")
            state.summarized_message_count = p.get("summarized_message_count", 0)
        elif ev.type == "run.completed":
            state.step = p["step"]
            state.answer = p["answer"]
            state.status = "done"
            state.add("assistant", p["answer"])
        elif ev.type == "run.failed":
            state.answer = p.get("answer")
            state.status = "failed"
            state.last_error = p.get("error_message") or p.get("error_type") or state.answer
        elif ev.type == "run.cancelled":
            state.answer = p["answer"]
            state.status = "cancelled"
        # tool.started / run.resumed / 其它不认识的类型：no-op，向前向后兼容
    return state
