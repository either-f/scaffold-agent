"""M2 最小 eval：离线门禁与真实 DeepSeek 基线。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from examples.run_demo import FILESYSTEM_READ_TOOLS, guard_example_fetch, m1_servers
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.model_litellm import LiteLLMModel
from agent_kernel.adapters.tools_mcp import McpToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import ToolPort
from agent_kernel.types import ToolResult, ToolSpec


class FakeEvalToolbox(ToolPort):
    def __init__(self, results: dict[str, str]) -> None:
        self.results = results

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name, "eval fake tool") for name in self.results]

    def call(self, name: str, args: dict) -> ToolResult:
        if name not in self.results:
            raise KeyError(name)
        return ToolResult(content=self.results[name].replace("{ROOT}", PROJECT_ROOT.as_posix()))


def load_tasks() -> list[dict]:
    path = PROJECT_ROOT / "evals" / "tasks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_task(task: dict, model, tools: ToolPort) -> dict:
    calls: list[dict] = []
    bus = EventBus()

    def record(event) -> None:
        if event.type == "tool.after":
            result = str(event.payload.get("result", ""))
            calls.append(
                {
                    "name": str(event.payload["tool"]),
                    "ok": not result.startswith(("[tool-error]", "[HITL]")),
                }
            )

    bus.subscribe("tool.after", record)
    try:
        state = AgentKernel(
            model=model,
            tools=tools,
            planner=ReactPlanner(),
            bus=bus,
            max_steps=int(task["max_steps"]),
        ).run(task["input"].replace("{ROOT}", PROJECT_ROOT.as_posix()))
        answer = state.answer or ""
        expect_error = task.get("expect_tool_error", False)
        successful = {call["name"] for call in calls if call["ok"]}
        called = {call["name"] for call in calls}
        reasons = []
        if state.status != "done":
            reasons.append(f"status={state.status}")
        if expect_error:
            missing = set(task["required_tools"]) - called
        else:
            missing = set(task["required_tools"]) - successful
        if missing:
            reasons.append(f"missing_tools={sorted(missing)}")
        if not expect_error and any(not call["ok"] for call in calls):
            reasons.append("tool_error")
        missing_text = [text for text in task["answer_contains"] if text.casefold() not in answer.casefold()]
        if missing_text:
            reasons.append(f"missing_text={missing_text}")
        if state.step > int(task["max_steps"]):
            reasons.append(f"steps={state.step}")
        return {
            "id": task["id"],
            "passed": not reasons,
            "steps": state.step,
            "tools": calls,
            "answer": answer,
            "reasons": reasons,
        }
    except Exception as exc:
        return {
            "id": task["id"],
            "passed": False,
            "steps": 0,
            "tools": calls,
            "answer": "",
            "reasons": [f"exception={type(exc).__name__}: {exc}"],
        }


def offline_results(tasks: list[dict]) -> list[dict]:
    results = []
    for task in tasks:
        script = [json.dumps(action, ensure_ascii=False) for action in task["fake_actions"]]
        results.append(run_task(task, FakeScriptedModel(script), FakeEvalToolbox(task["fake_results"])))
    return results


def deepseek_results(tasks: list[dict]) -> list[dict]:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    allow = FILESYSTEM_READ_TOOLS | {"fetch.fetch"}
    model = LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0)
    with McpToolbox(
        m1_servers(),
        allow=allow,
        guards={"fetch.fetch": guard_example_fetch},
        request_timeout_seconds=30,
        retryable=allow,
        max_retries=1,
    ) as tools:
        return [run_task(task, model, tools) for task in tasks]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "deepseek"), default="offline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    tasks = load_tasks()
    if len(tasks) < 10:
        raise RuntimeError(f"eval 至少需要 10 条任务，当前为 {len(tasks)}")
    results = offline_results(tasks) if args.mode == "offline" else deepseek_results(tasks)
    passed = sum(result["passed"] for result in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "model": "fake-scripted" if args.mode == "offline" else "deepseek/deepseek-chat",
        "passed": passed,
        "total": len(results),
        "average_steps": round(sum(result["steps"] for result in results) / len(results), 2),
        "results": results,
    }
    for result in results:
        marker = "PASS" if result["passed"] else "FAIL"
        print(f"[{marker}] {result['id']} steps={result['steps']} reasons={result['reasons']}")
    print(f"EVAL {args.mode}: {passed}/{len(results)}, average_steps={report['average_steps']}")

    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    threshold = len(tasks) if args.mode == "offline" else max(8, int(len(tasks) * 0.8))
    return 0 if passed >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
