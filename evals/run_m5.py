"""M5 策略基线：离线对比 ReAct / PlanExecute / CodeAct / LangGraph。

用确定性 fake-model 与 fake-graph 跑等量代表性用例，不触网、不造假时序。
输出 JSON 含 generated_at、per-strategy passed/total/average_steps、复杂度备注与整体 ok。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.sandbox_docker import DockerSandbox, SandboxToolbox
from agent_kernel.adapters.tools.local import LocalToolbox, default_toolbox, safe_calc
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.codeact import CodeActPlanner
from agent_kernel.planners.dag import DagPlanner
from agent_kernel.planners.langgraph import LangGraphPlanner
from agent_kernel.planners.plan_execute import PlanExecutePlanner
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import RunState, ToolCall


# ----------------------------------------------------------------- fake runner
def _fake_runner_factory(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return runner


# ----------------------------------------------------------------- case defs
def _react_cases() -> list[dict]:
    return [
        {
            "name": "arithmetic-single-step",
            "prompt": "算一下 (3+4)*7",
            "script": [
                '{"thought": "需要计算", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
                '{"thought": "结果是 49", "final": "结果是 49"}',
            ],
            "answer_contains": ["49"],
        },
        {
            "name": "tool-selection-now",
            "prompt": "现在几点？",
            "script": [
                '{"thought": "获取时间", "tool": "now", "args": {}}',
                '{"thought": "时间已获取", "final": "当前时间见工具结果。"}',
            ],
            "answer_contains": ["时间"],
        },
        {
            "name": "multi-step-calc",
            "prompt": "先算 2+3，再算结果乘 4",
            "script": [
                '{"thought": "第一步加法", "tool": "calc", "args": {"expression": "2+3"}}',
                '{"thought": "第二步乘法", "tool": "calc", "args": {"expression": "5*4"}}',
                '{"thought": "结果是 20", "final": "2+3=5，5*4=20，最终结果是 20。"}',
            ],
            "answer_contains": ["20"],
        },
    ]


def _plan_execute_cases() -> list[dict]:
    return [
        {
            "name": "arithmetic-with-plan",
            "prompt": "算一下 (3+4)*7",
            "script": [
                '{"thought": "先计划再算", "plan": "1. 用 calc 计算 (3+4)*7", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
                '{"thought": "结果是 49", "final": "结果是 49"}',
            ],
            "answer_contains": ["49"],
        },
        {
            "name": "plan-then-execute",
            "prompt": "先算 6*7，再确认结果",
            "script": [
                '{"thought": "制定计划", "plan": "1. 用 calc 计算 6*7；2. 确认结果", "tool": "calc", "args": {"expression": "6*7"}}',
                '{"thought": "确认结果", "plan": "结果已确认", "final": "6*7 的结果是 42。"}',
            ],
            "answer_contains": ["42"],
        },
        {
            "name": "plan-empty-fallback",
            "prompt": "算 10-3",
            "script": [
                '{"thought": "没有计划字段", "tool": "calc", "args": {"expression": "10-3"}}',
                '{"thought": "t", "final": "10-3 的结果是 7。"}',
            ],
            "answer_contains": ["7"],
        },
    ]


def _codeact_cases() -> list[dict]:
    return [
        {
            "name": "codeact-arithmetic",
            "prompt": "算一下 (3+4)*7",
            "script": [
                '{"thought": "写代码计算", "tool": "python_execute", "args": {"code": "print((3+4)*7)"}}',
                '{"thought": "结果是 49", "final": "结果是 49"}',
            ],
            "stdout": "49\n",
            "answer_contains": ["49"],
        },
        {
            "name": "codeact-list-comprehension",
            "prompt": "计算 1 到 5 的平方和",
            "script": [
                '{"thought": "写代码计算平方和", "tool": "python_execute", "args": {"code": "print(sum(x**2 for x in range(1,6)))"}}',
                '{"thought": "结果是 55", "final": "1 到 5 的平方和是 55。"}',
            ],
            "stdout": "55\n",
            "answer_contains": ["55"],
        },
        {
            "name": "codeact-string-manipulation",
            "prompt": "把 hello world 反转",
            "script": [
                '{"thought": "写代码反转字符串", "tool": "python_execute", "args": {"code": "print(\'hello world\'[::-1])"}}',
                '{"thought": "结果是 dlrow olleh", "final": "反转后是 dlrow olleh。"}',
            ],
            "stdout": "dlrow olleh\n",
            "answer_contains": ["dlrow"],
        },
    ]


# ----------------------------------------------------------------- LangGraph
class _FakeGraph:
    def __init__(self, outputs: list) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict] = []

    def invoke(self, payload, *args, **kwargs):
        self.calls.append(payload)
        return self._outputs.pop(0) if self._outputs else {}


def _langgraph_cases() -> list[dict]:
    return [
        {
            "name": "langgraph-final-branch",
            "prompt": "直接回答问题",
            "graph_outputs": [{"final": "图给出的答案", "thought": "完成"}],
            "answer_contains": ["图给出的答案"],
        },
        {
            "name": "langgraph-tool-branch",
            "prompt": "算 5*8",
            "graph_outputs": [
                {"tool": "calc", "args": {"expression": "5*8"}, "thought": "算一下"},
                {"final": "40"},
            ],
            "answer_contains": ["40"],
        },
        {
            "name": "langgraph-hitl-shaped",
            "prompt": "需要审批后执行计算",
            "graph_outputs": [
                {"tool": "calc", "args": {"expression": "12*12"}, "thought": "需要审批"},
                {"final": "144，审批已通过。"},
            ],
            "answer_contains": ["144", "审批"],
        },
    ]


def _flaky_toolbox() -> LocalToolbox:
    box = default_toolbox()
    state = {"calls": 0}

    def flaky_calc(expression: str) -> str:
        state["calls"] += 1
        if state["calls"] <= 1:
            raise RuntimeError("暂时不可用")
        return safe_calc(expression)

    box.register(
        "flaky_calc", "偶发失败的计算器（演示 Harness 重试）", flaky_calc,
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
    )
    return box


def _dag_cases() -> list[dict]:
    return [
        {
            "name": "dag-parallel-two-nodes",
            "prompt": "同时算 2+2 和 3+3",
            "decompose": (
                '{"nodes": ['
                '{"id": "n1", "tool": "calc", "args": {"expression": "2+2"}, "depends_on": []}, '
                '{"id": "n2", "tool": "calc", "args": {"expression": "3+3"}, "depends_on": []}'
                ']}'
            ),
            "synth": '{"final": "2+2=4，3+3=6"}',
            "answer_contains": ["4", "6"],
            "toolbox": "default",
        },
        {
            "name": "dag-dependent-wave",
            "prompt": "算两个独立算式，再汇总",
            "decompose": (
                '{"nodes": ['
                '{"id": "n1", "tool": "calc", "args": {"expression": "1+1"}, "depends_on": []}, '
                '{"id": "n2", "tool": "calc", "args": {"expression": "2+2"}, "depends_on": []}, '
                '{"id": "n3", "tool": "now", "args": {}, "depends_on": ["n1", "n2"]}'
                ']}'
            ),
            "synth": '{"final": "1+1=2，2+2=4，汇总完成"}',
            "answer_contains": ["2", "4", "汇总"],
            "toolbox": "default",
        },
        {
            "name": "dag-harness-retry-fallback",
            "prompt": "算一下 5*5，工具偶发不稳定",
            "decompose": (
                '{"nodes": ['
                '{"id": "n1", "tool": "flaky_calc", "args": {"expression": "5*5"}, "depends_on": [], '
                '"max_attempts": 2, "backoff_base": 0.01}'
                ']}'
            ),
            "synth": '{"final": "5*5=25（首次失败，Harness 重试后成功）"}',
            "answer_contains": ["25"],
            "toolbox": "flaky",
        },
    ]


# ----------------------------------------------------------------- runners
def _run_react(case: dict) -> dict:
    model = FakeScriptedModel(list(case["script"]))
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    state = kernel.run(case["prompt"])
    answer = state.answer or ""
    passed = state.status == "done" and all(
        kw.casefold() in answer.casefold() for kw in case["answer_contains"]
    )
    return {"name": case["name"], "passed": passed, "steps": state.step, "answer": answer}


def _run_plan_execute(case: dict) -> dict:
    model = FakeScriptedModel(list(case["script"]))
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=PlanExecutePlanner(),
    )
    state = kernel.run(case["prompt"])
    answer = state.answer or ""
    passed = state.status == "done" and all(
        kw.casefold() in answer.casefold() for kw in case["answer_contains"]
    )
    return {"name": case["name"], "passed": passed, "steps": state.step, "answer": answer}


def _run_codeact(case: dict) -> dict:
    sandbox = DockerSandbox(runner=_fake_runner_factory(stdout=case.get("stdout", "49\n")))
    model = FakeScriptedModel(list(case["script"]))
    kernel = AgentKernel(
        model=model,
        tools=SandboxToolbox(sandbox),
        planner=CodeActPlanner(),
    )
    state = kernel.run(case["prompt"])
    answer = state.answer or ""
    passed = state.status == "done" and all(
        kw.casefold() in answer.casefold() for kw in case["answer_contains"]
    )
    return {"name": case["name"], "passed": passed, "steps": state.step, "answer": answer}


def _run_langgraph(case: dict) -> dict:
    graph = _FakeGraph(case["graph_outputs"])
    kernel = AgentKernel(
        model=FakeScriptedModel([]),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
    )
    state = kernel.run(case["prompt"])
    answer = state.answer or ""
    passed = state.status == "done" and all(
        kw.casefold() in answer.casefold() for kw in case["answer_contains"]
    )
    return {"name": case["name"], "passed": passed, "steps": state.step, "answer": answer}


def _run_dag(case: dict) -> dict:
    model = FakeScriptedModel([case["decompose"], case["synth"]])
    tools = _flaky_toolbox() if case["toolbox"] == "flaky" else default_toolbox()
    kernel = AgentKernel(model=model, tools=tools, planner=DagPlanner())
    state = kernel.run(case["prompt"])
    answer = state.answer or ""
    passed = state.status == "done" and all(
        kw.casefold() in answer.casefold() for kw in case["answer_contains"]
    )
    return {"name": case["name"], "passed": passed, "steps": state.step, "answer": answer}


# ----------------------------------------------------------------- strategy meta
COMPLEXITY_NOTES = {
    "react": "ReAct：单步思考-动作循环，JSON 协议解析，无计划开销，步数等于工具调用+final。",
    "plan_execute": "Plan-Execute：首步额外生成计划并存入上下文，后续步骤复用 ReAct；计划不增加工具调用但增加 prompt 长度。",
    "codeact": "CodeAct：模型写 Python 代码经 Docker 沙箱执行，指令模板替换为 python_execute；沙箱安全隔离无网络无挂载。",
    "langgraph": "LangGraph：注入外部编译图，翻译图输出为 ToolCall/FinalAnswer；HITL 形态以工具分支模拟审批后执行。",
    "dag": "DAG：任务拆解成带依赖的节点图，一次 kernel 步骤内按拓扑序分批并行执行独立节点"
    "（ThreadPoolExecutor），节点级 Harness（超时+重试退避+fallback）与 Race Strategy"
    "（多候选并发取最快成功）；kernel 只看到一次 FinalAnswer，step 数恒为 1，不反映内部并行节点数。",
}


def _strategy_report(name: str, results: list[dict]) -> dict:
    passed = sum(r["passed"] for r in results)
    total = len(results)
    avg_steps = round(sum(r["steps"] for r in results) / total, 2) if total else 0
    return {
        "strategy": name,
        "passed": passed,
        "total": total,
        "average_steps": avg_steps,
        "complexity": COMPLEXITY_NOTES[name],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 策略离线对比基线")
    parser.add_argument("--output", type=Path, help="写入 JSON 基线文件路径")
    args = parser.parse_args()

    react_results = [_run_react(c) for c in _react_cases()]
    plan_results = [_run_plan_execute(c) for c in _plan_execute_cases()]
    codeact_results = [_run_codeact(c) for c in _codeact_cases()]
    langgraph_results = [_run_langgraph(c) for c in _langgraph_cases()]
    dag_results = [_run_dag(c) for c in _dag_cases()]

    strategies = [
        _strategy_report("react", react_results),
        _strategy_report("plan_execute", plan_results),
        _strategy_report("codeact", codeact_results),
        _strategy_report("langgraph", langgraph_results),
        _strategy_report("dag", dag_results),
    ]

    overall_passed = sum(s["passed"] for s in strategies)
    overall_total = sum(s["total"] for s in strategies)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline",
        "model": "fake-scripted",
        "strategies": strategies,
        "overall_passed": overall_passed,
        "overall_total": overall_total,
        "ok": overall_passed == overall_total,
    }

    for s in strategies:
        print(f"[{s['strategy']}] {s['passed']}/{s['total']} avg_steps={s['average_steps']}")
        for r in s["results"]:
            marker = "PASS" if r["passed"] else "FAIL"
            print(f"  [{marker}] {r['name']} steps={r['steps']}")
    print(f"M5 overall: {overall_passed}/{overall_total}, ok={report['ok']}")

    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
