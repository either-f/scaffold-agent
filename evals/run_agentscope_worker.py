"""M10 真实 agentscope worker eval：真实 DeepSeek 驱动的 agentscope.Agent 被本仓库
AgentKernel 通过 WorkerDelegationPort 委派任务，验证跨框架 worker 互操作零改内核。
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_agentscope_worker() -> dict:
    from agentscope.agent import Agent
    from agentscope.credential import DeepSeekCredential
    from agentscope.model import DeepSeekChatModel

    from agent_kernel.adapters.model.litellm import LiteLLMModel
    from agent_kernel.adapters.tools.agents import WorkerDelegationPort
    from agent_kernel.adapters.tools.agentscope_worker import AgentScopeWorker
    from agent_kernel.adapters.tools.local import LocalToolbox
    from agent_kernel.kernel import AgentKernel
    from agent_kernel.planners.react import ReactPlanner

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    agentscope_agent = Agent(
        name="math-worker",
        system_prompt="你是一个只回答数学计算结果的助手，直接给出数字答案。",
        model=DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key),
            model="deepseek-chat",
            stream=False,
        ),
    )
    worker = AgentScopeWorker(agentscope_agent)

    parent_tools = WorkerDelegationPort(LocalToolbox())
    parent_tools.register("math", worker, "将数学计算任务委派给 agentscope 驱动的 math worker")
    parent = AgentKernel(
        model=LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0),
        tools=parent_tools,
        planner=ReactPlanner(),
        max_steps=4,
    )

    state = parent.run("请委托 math worker 帮我计算 123 * 456，把结果直接告诉我。")
    answer = state.answer or ""
    ok = state.status == "done" and "56088" in answer.replace(",", "")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "agentscope_worker_real",
        "parent_model": "deepseek/deepseek-chat (LiteLLMModel)",
        "worker_model": "deepseek-chat (agentscope DeepSeekChatModel)",
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

    report = run_agentscope_worker()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
