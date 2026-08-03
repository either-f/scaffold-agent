"""SPEC-94 事件身份/排序、critical-vs-best-effort 投递、reducer 完整性测试。

运行：PYTHONPATH=src python -m pytest tests/test_events.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.event_store import SqliteEventStore
from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.tools.local import LocalToolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.event_sourcing import reduce
from agent_kernel.events import EventBus
from agent_kernel.fork import fork
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import ArtifactRef, Event, RunState, ToolResult

TOOL_SCRIPT = '{"thought": "t", "tool": "calc", "args": {"expression": "2+2"}}'
FINAL_SCRIPT = '{"thought": "t", "final": "结果是 4"}'


def _kernel(tmp, script, approval=None, max_steps=10, bus=None):
    store = JsonCheckpointStore(str(Path(tmp) / "runs"))
    return (
        AgentKernel(
            model=FakeScriptedModel(list(script)),
            tools=_toolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
            bus=bus or EventBus(),
            approval=approval,
            max_steps=max_steps,
        ),
        store,
    )


def _toolbox():
    box = LocalToolbox()
    box.register("calc", "calc", lambda expression: str(eval(expression)))
    return box


# --------------------------------------------------------------- Event identity

def test_event_has_event_id_and_sequence_defaults():
    ev = Event("x", {"a": 1})
    assert ev.event_id  # uuid4 hex 非空
    assert ev.schema_version == 1
    assert ev.sequence == 0


def test_emit_stamps_event_id_and_per_run_sequence():
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus()
        captured: list[Event] = []
        bus.subscribe("*", lambda e: captured.append(e))
        kernel, _ = _kernel(tmp, [FINAL_SCRIPT], bus=bus)
        kernel.run("hi", state=RunState(run_id="seq-run"))

        assert len(captured) >= 2
        for ev in captured:
            assert ev.event_id  # 非空
        seqs = [ev.sequence for ev in captured]
        assert seqs == sorted(seqs)  # 单调不减
        assert seqs[-1] >= len(captured)  # 至少跟事件数一样多


def test_sequence_scoped_per_run_id():
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus()
        captured: list[Event] = []
        bus.subscribe("*", lambda e: captured.append(e))
        kernel, _ = _kernel(tmp, [FINAL_SCRIPT], bus=bus)
        kernel.run("first", state=RunState(run_id="run-A"))
        kernel2, _ = _kernel(tmp, [FINAL_SCRIPT], bus=bus)
        kernel2.run("second", state=RunState(run_id="run-B"))

        seqs_a = [e.sequence for e in captured if e.payload.get("run_id") == "run-A"]
        seqs_b = [e.sequence for e in captured if e.payload.get("run_id") == "run-B"]
        # 两个 run 各自从 1 开始，互不影响
        assert seqs_a[0] == 1
        assert seqs_b[0] == 1


# --------------------------------------------------------- Tiered delivery

def test_best_effort_subscriber_failure_swallowed():
    bus = EventBus()
    bus.subscribe("test", lambda e: (_ for _ in ()).throw(ValueError("boom")))
    # 不应抛
    bus.publish(Event("test", {}))


def test_critical_subscriber_failure_propagates():
    bus = EventBus()
    bus.subscribe("test", lambda e: 1 / 0, critical=True)
    with pytest.raises(ZeroDivisionError):
        bus.publish(Event("test", {}))


def test_critical_failure_does_not_block_best_effort():
    bus = EventBus()
    best_effort_ran = []
    bus.subscribe("test", lambda e: best_effort_ran.append(1))
    bus.subscribe("test", lambda e: (_ for _ in ()).throw(RuntimeError("critical fail")), critical=True)
    with pytest.raises(RuntimeError):
        bus.publish(Event("test", {}))
    assert best_effort_ran == [1]  # best-effort 先跑完


def test_critical_and_best_effort_same_event_type():
    """SPEC-94 AC: 一个 critical + 一个 best-effort 订阅同一事件类型。"""
    bus = EventBus()
    be_calls = []
    bus.subscribe("evt", lambda e: be_calls.append(e.payload["n"]))
    bus.subscribe("evt", lambda e: (_ for _ in ()).throw(ValueError("critical err")), critical=True)
    with pytest.raises(ValueError):
        bus.publish(Event("evt", {"n": 42}))
    assert be_calls == [42]


# --------------------------------------------------------- Reducer coverage

def test_reducer_handles_run_failed_error_detail():
    events = [
        Event("run.started", {"run_id": "r1", "turn": 1, "input": "hi"}),
        Event("run.failed", {
            "run_id": "r1",
            "step": 3,
            "answer": "已达最大步数限制。",
            "error_type": "MaxStepsExhausted",
            "error_message": "已达最大步数限制。",
        }),
    ]
    state = reduce(events)
    assert state.status == "failed"
    assert state.answer == "已达最大步数限制。"
    assert state.last_error == "已达最大步数限制。"


def test_reducer_handles_context_summarized():
    events = [
        Event("run.started", {"run_id": "r1", "turn": 1, "input": "hi"}),
        Event("context.summarized", {
            "run_id": "r1",
            "context_summary": "压缩后的摘要",
            "summarized_message_count": 5,
        }),
    ]
    state = reduce(events)
    assert state.context_summary == "压缩后的摘要"
    assert state.summarized_message_count == 5


def test_reducer_handles_run_forked():
    events = [
        Event("run.forked", {
            "run_id": "fork-1",
            "forked_from": "src@turn_001_step_001",
            "source_run_id": "src",
            "checkpoint": "turn_001_step_001",
            "turn": 1,
            "step": 1,
        }),
    ]
    state = reduce(events)
    assert state.run_id == "fork-1"
    assert state.forked_from == "src@turn_001_step_001"
    assert state.turn == 1
    assert state.step == 1


# ----------------------------------------------- Full round-trip: event store

def test_event_store_roundtrips_new_fields():
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteEventStore(str(Path(tmp) / "ev.db"))
        ev = Event("test", {"run_id": "r1", "x": 1}, event_id="abc123", schema_version=1, sequence=7)
        store.handler()(ev)
        loaded = store.load_events("r1")
        store.close()
        assert len(loaded) == 1
        assert loaded[0].event_id == "abc123"
        assert loaded[0].sequence == 7
        assert loaded[0].schema_version == 1


def test_event_store_migration_from_old_schema():
    """旧库（无 event_id/schema_version/sequence 列）能自动迁移并读取。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "old.db")
        # 手动建一个旧 schema 的库
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, ts REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO events (run_id, type, payload, ts) VALUES ('r1', 'test', '{}', 0.0)"
        )
        conn.commit()
        conn.close()

        # 用新代码打开 -> 自动 ALTER TABLE
        store = SqliteEventStore(db_path)
        loaded = store.load_events("r1")
        store.close()
        assert len(loaded) == 1
        # 旧行用默认值
        assert loaded[0].event_id == ""
        assert loaded[0].sequence == 0
        assert loaded[0].schema_version == 1


# --------------------------------------------------- Fork + reducer integration

def test_fork_emits_run_forked_event():
    """fork() 带 bus 时发 run.forked 事件，reduce 能重建 forked_from。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(str(Path(tmp) / "runs"))
        bus = EventBus()
        captured: list[Event] = []
        bus.subscribe("*", lambda e: captured.append(e))

        kernel = AgentKernel(
            model=FakeScriptedModel([TOOL_SCRIPT]),
            tools=_toolbox(),
            planner=ReactPlanner(),
            checkpoints=store,
            bus=bus,
            approval=lambda c: (_ for _ in ()).throw(SystemExit("crash")),
            max_steps=4,
        )
        try:
            kernel.run("算一下", state=RunState(run_id="fork-src"))
        except SystemExit:
            pass

        state_forked = fork(store, "fork-src", "turn_001_step_001", new_run_id="fork-1", bus=bus)
        forked_events = [e for e in captured if e.type == "run.forked"]
        assert len(forked_events) == 1
        assert forked_events[0].payload["run_id"] == "fork-1"
        assert forked_events[0].payload["forked_from"] == "fork-src@turn_001_step_001"
        assert forked_events[0].payload["turn"] == state_forked.turn
        assert forked_events[0].payload["step"] == state_forked.step

        # reduce 从 run.forked 事件重建 forked_from
        reduced = reduce(forked_events)
        assert reduced.run_id == "fork-1"
        assert reduced.forked_from == "fork-src@turn_001_step_001"


# ----------------------------------------------------- Full run + reduce match

def test_full_run_reduce_matches_live_state():
    """完整 run 的事件流经 reduce 重建后，关键字段与 live RunState 一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus()
        store_path = str(Path(tmp) / "ev.db")
        sqlite_store = SqliteEventStore(store_path)
        bus.subscribe("*", sqlite_store.handler())

        kernel, _ = _kernel(tmp, [TOOL_SCRIPT, FINAL_SCRIPT], approval=lambda c: True, bus=bus)
        state = kernel.run("算 2+2", state=RunState(run_id="full-1"))

        events = sqlite_store.load_events("full-1")
        sqlite_store.close()

        reduced = reduce(events)
        assert reduced.run_id == state.run_id
        assert reduced.status == state.status
        assert reduced.answer == state.answer
        assert reduced.step == state.step
        assert reduced.messages == state.messages


def test_max_steps_run_reduce_preserves_last_error():
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus()
        store_path = str(Path(tmp) / "ev.db")
        sqlite_store = SqliteEventStore(store_path)
        bus.subscribe("*", sqlite_store.handler())

        kernel, _ = _kernel(tmp, [TOOL_SCRIPT] * 5, approval=lambda c: True, max_steps=2, bus=bus)
        state = kernel.run("loop", state=RunState(run_id="fail-1"))

        events = sqlite_store.load_events("fail-1")
        sqlite_store.close()

        reduced = reduce(events)
        assert reduced.status == "failed"
        assert reduced.last_error is not None
        assert reduced.last_error == state.last_error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
