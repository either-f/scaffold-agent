"""从事件重建 RunState（ADR-0009）。纯标准库，只依赖 .types。

已知边界：context_summary/summarized_message_count 由 ContextBuilder（planner
侧）改写，不在内核的状态变更点里，这里重建不出来，停在默认值。
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
            state.pending_tool = None
            state.pending_effect_id = None
            state.add("user", p["input"])
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
            state.add("tool", p["result"], name=p["tool"])
            state.pending_tool = None
            state.pending_effect_id = None
        elif ev.type == "run.completed":
            state.step = p["step"]
            state.answer = p["answer"]
            state.status = "done"
            state.add("assistant", p["answer"])
        elif ev.type == "run.failed":
            state.answer = p["answer"]
            state.status = "failed"
        # tool.started / run.resumed / 其它不认识的类型：no-op，向前向后兼容
    return state
