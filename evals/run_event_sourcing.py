"""事件溯源验证（ADR-0009）：reduce(事件) 重建出的 RunState 要跟真实
checkpoint 逐字段相等——证明事件流是完整、正确的事实来源，而不只是审计噪音。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.event_store import SqliteEventStore
from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.event_sourcing import reduce
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import ModelPort
from agent_kernel.types import ModelOutput, RunState

TOOL_CALL_SCRIPT = json.dumps({"thought": "算一下", "tool": "calc", "args": {"expression": "2+2"}})
FINAL_SCRIPT = json.dumps({"thought": "完成", "final": "结果是 4"})


class FakeModel(ModelPort):
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, tools) -> ModelOutput:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return ModelOutput(self.script[idx])


def _make_toolbox() -> LocalToolbox:
    tools = LocalToolbox()
    tools.register("calc", "计算表达式", lambda expression: str(eval(expression)))
    return tools


def _run_scenario(run_id: str, script: list[str], approval, max_steps: int = 10) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        checkpoints = JsonCheckpointStore(str(Path(tmp) / "runs"))
        store = SqliteEventStore(str(Path(tmp) / "events.db"))
        bus = EventBus()
        bus.subscribe("*", store.handler())

        kernel = AgentKernel(
            model=FakeModel(script),
            tools=_make_toolbox(),
            planner=ReactPlanner(),
            checkpoints=checkpoints,
            bus=bus,
            approval=approval,
            max_steps=max_steps,
        )
        final_state = kernel.run("帮我算 2+2", state=RunState(run_id=run_id))

        events = store.load_events(run_id)
        reduced = reduce(events)
        checkpointed = checkpoints.load(run_id)
        store.close()

        fields_match = (
            checkpointed is not None
            and reduced.run_id == checkpointed.run_id
            and reduced.messages == checkpointed.messages
            and reduced.status == checkpointed.status
            and reduced.answer == checkpointed.answer
            and reduced.pending_tool == checkpointed.pending_tool
            and reduced.pending_effect_id == checkpointed.pending_effect_id
            and reduced.step == checkpointed.step
        )
        return {
            "run_id": run_id,
            "event_count": len(events),
            "event_types": [e.type for e in events],
            "final_status": final_state.status,
            "fields_match": fields_match,
            "ok": fields_match,
        }


def scenario_approved() -> dict:
    result = _run_scenario(
        "es-approved", [TOOL_CALL_SCRIPT, FINAL_SCRIPT], approval=lambda call: True
    )
    result["name"] = "tool_call_approved"
    return result


def scenario_rejected() -> dict:
    result = _run_scenario(
        "es-rejected", [TOOL_CALL_SCRIPT, FINAL_SCRIPT], approval=lambda call: False
    )
    result["name"] = "tool_call_rejected"
    return result


def scenario_max_steps() -> dict:
    # 脚本只会一直吐工具调用，永不终答，逼到步数耗尽 -> run.failed
    result = _run_scenario(
        "es-maxsteps", [TOOL_CALL_SCRIPT], approval=lambda call: True, max_steps=2
    )
    result["name"] = "max_steps_exhausted"
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios = [scenario_approved(), scenario_rejected(), scenario_max_steps()]
    ok = all(s["ok"] for s in scenarios)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "event-sourcing",
        "scenarios": scenarios,
        "ok": ok,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
