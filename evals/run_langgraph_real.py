"""M5 真实 LangGraph StateGraph 验证：编译一个真正的 langgraph 图（非脚本化假图），
喂给 LangGraphPlanner，跑 final / tool / HITL 三条真实分支。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_langgraph_real() -> dict:
    from agent_kernel.adapters.langgraph_demo_graph import build_graph
    from agent_kernel.adapters.tools.local import default_toolbox
    from agent_kernel.events import EventBus
    from agent_kernel.kernel import AgentKernel
    from agent_kernel.planners.langgraph import LangGraphPlanner
    from agent_kernel.types import ToolCall

    class NeverCalledModel:
        """LangGraphPlanner 不应该调用 model.complete()；用会报错的假模型确保这一点。"""

        def complete(self, messages, tools):
            raise AssertionError("LangGraphPlanner 不应调用 model.complete()")

    graph = build_graph()  # 真实编译的 langgraph StateGraph，三个 case 共用同一个编译图对象

    results = {}

    # ---- final 分支：无算术表达式，classify 直接路由到 answer_direct
    kernel_final = AgentKernel(
        model=NeverCalledModel(),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
        max_steps=3,
    )
    state_final = kernel_final.run("你好，你是谁？")
    results["final_branch"] = {
        "status": state_final.status,
        "steps": state_final.step,
        "answer": state_final.answer,
        "ok": state_final.status == "done" and state_final.step == 1 and bool(state_final.answer),
    }

    # ---- tool 分支：含算术表达式，走 call_tool -> 真实执行 calc -> answer_with_result
    kernel_tool = AgentKernel(
        model=NeverCalledModel(),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
        max_steps=4,
    )
    state_tool = kernel_tool.run("帮我算一下 5*8 等于多少")
    results["tool_branch"] = {
        "status": state_tool.status,
        "steps": state_tool.step,
        "answer": state_tool.answer,
        "ok": state_tool.status == "done" and state_tool.step == 2 and "40" in (state_tool.answer or ""),
    }

    # ---- HITL 分支：同样是 tool 分支，但内核挂了 approval 回调，验证真实图产出的
    # ToolCall 会真的触发内核的审批拦截（approval 由 kernel.py 同步调用，非脚本模拟）
    bus = EventBus()
    approval_events: list[dict] = []
    bus.subscribe("tool.approval", lambda e: approval_events.append(dict(e.payload)))

    def approve(action: ToolCall) -> bool:
        return True

    kernel_hitl = AgentKernel(
        model=NeverCalledModel(),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
        bus=bus,
        approval=approve,
        max_steps=4,
    )
    state_hitl = kernel_hitl.run("帮我算一下 12*12 等于多少")
    results["hitl_branch"] = {
        "status": state_hitl.status,
        "steps": state_hitl.step,
        "answer": state_hitl.answer,
        "approval_events": approval_events,
        "ok": (
            state_hitl.status == "done"
            and "144" in (state_hitl.answer or "")
            and len(approval_events) == 1
            and approval_events[0].get("approved") is True
        ),
    }

    ok = all(r["ok"] for r in results.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "langgraph-real",
        "graph": "真实编译的 langgraph.graph.StateGraph（4 节点 + 条件边），非脚本化假图",
        "final_branch": results["final_branch"],
        "tool_branch": results["tool_branch"],
        "hitl_branch": results["hitl_branch"],
        "ok": ok,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_langgraph_real()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
