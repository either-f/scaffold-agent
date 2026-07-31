"""M6 真实 Worker 委派 eval：真实 DeepSeek 双层 AgentKernel 验证 WorkerDelegationPort。

补上 README 坦诚声明里的缺口：Worker 委派此前只在 demo 里用 FakeScriptedModel 验证过，
没有真实生产 LLM 的证据。
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_worker_real() -> dict:
    from agent_kernel.adapters.model_litellm import LiteLLMModel
    from agent_kernel.adapters.tools_agents import WorkerDelegationPort
    from agent_kernel.adapters.tools_local import LocalToolbox, safe_calc
    from agent_kernel.kernel import AgentKernel
    from agent_kernel.planners.react import ReactPlanner

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    def model() -> LiteLLMModel:
        return LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0)

    worker_tools = LocalToolbox()
    worker_tools.register("calc", "计算四则运算表达式", lambda expression: safe_calc(expression))
    worker = AgentKernel(model=model(), tools=worker_tools, planner=ReactPlanner(), max_steps=4)

    parent_tools = WorkerDelegationPort(LocalToolbox())
    parent_tools.register("math", worker, "将数学计算任务委派给 math worker")
    parent = AgentKernel(model=model(), tools=parent_tools, planner=ReactPlanner(), max_steps=4)

    state = parent.run("请委托 math worker 帮我计算 123 * 456，把结果直接告诉我。")
    answer = state.answer or ""
    ok = state.status == "done" and "56088" in answer.replace(",", "")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "worker_real",
        "model": "deepseek/deepseek-chat",
        "parent_status": state.status,
        "parent_steps": state.step,
        "answer": answer,
        "expected": "56088",
        "ok": ok,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_worker_real()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
