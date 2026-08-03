"""M5 真实 OpenTelemetry 观测验证：用真实 opentelemetry-sdk（非 mock）生成 span，
断言真实 span 数据，而不是只验证 handler 被调用。

SPEC-85: 除原有 span-count/attribute 检查外，新增 parent/child span 关系断言——
验证 Run → Step → Model/Tool 的真实父子 trace tree，而非扁平 span 列表。同时
验证 model.complete span 现在携带 run_id 属性（经 kernel.current_run_id
ContextVar 注入），使并发 run 下 CostLedger 归因正确。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_otel_real() -> dict:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agent_kernel.adapters.model.fake import FakeScriptedModel
    from agent_kernel.adapters.observability import ObservedModel, OtelExporter
    from agent_kernel.adapters.tools.local import LocalToolbox, safe_calc
    from agent_kernel.events import EventBus
    from agent_kernel.kernel import AgentKernel
    from agent_kernel.planners.react import ReactPlanner

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "agent-kernel-eval"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agent-kernel-eval")

    bus = EventBus()
    otel = OtelExporter(tracer)
    bus.subscribe("*", otel.handler())

    scripted_model = FakeScriptedModel(
        [
            json.dumps({"thought": "算一下", "tool": "calc", "args": {"expression": "2+2"}}),
            json.dumps({"thought": "完成", "final": "4"}),
        ]
    )
    model = ObservedModel(scripted_model, bus)

    tools = LocalToolbox()
    tools.register("calc", "四则运算", lambda expression: safe_calc(expression))

    kernel = AgentKernel(model=model, tools=tools, planner=ReactPlanner(), bus=bus, max_steps=3)
    state = kernel.run("帮我算 2+2")

    spans = exporter.get_finished_spans()
    span_names = sorted(s.name for s in spans)
    model_complete_spans = [s for s in spans if s.name == "model.complete"]
    tool_before_spans = [s for s in spans if s.name == "tool.before"]
    tool_after_spans = [s for s in spans if s.name == "tool.after"]
    run_start_spans = [s for s in spans if s.name == "run.start"]
    run_end_spans = [s for s in spans if s.name == "run.end"]
    step_start_spans = [s for s in spans if s.name == "step.start"]

    has_token_attrs = bool(model_complete_spans) and all(
        "prompt_tokens" in s.attributes and "duration_ms" in s.attributes
        for s in model_complete_spans
    )
    # tool.before 的 args 是 dict，OTel 原生只接受标量/同类序列；验证 _otel_attributes
    # 把它序列化成了 JSON 字符串塞进 span，而不是被 SDK 静默丢弃
    tool_before_args = tool_before_spans[0].attributes.get("args", "") if tool_before_spans else ""
    args_captured = '"expression"' in tool_before_args and "2+2" in tool_before_args

    # ---- SPEC-85: run_id 属性检查 ----
    # model.complete 现在应携带 run_id（经 kernel.current_run_id ContextVar 注入）
    model_complete_has_run_id = bool(model_complete_spans) and all(
        "run_id" in s.attributes and s.attributes["run_id"] == state.run_id
        for s in model_complete_spans
    )

    # ---- SPEC-85: parent/child span 关系检查 ----
    # 期望 trace tree:
    #   run.start (根, parent=None)
    #     ├─ step.start (parent=run.start)
    #     │    ├─ model.complete (parent=step.start)
    #     │    ├─ tool.before (parent=step.start)
    #     │    └─ tool.after (parent=step.start)
    #     ├─ step.start (parent=run.start)
    #     │    └─ model.complete (parent=step.start)
    #     └─ run.end (parent=None 或 run.start，取决于实现)
    by_id = {s.context.span_id: s for s in spans}

    def parent_id(s) -> str | None:
        p = getattr(s, "parent", None)
        if p is None:
            return None
        # parent 可能是 SpanContext 对象或 None
        return getattr(p, "span_id", None)

    run_start_span = run_start_spans[0] if run_start_spans else None
    run_start_is_root = run_start_span is not None and parent_id(run_start_span) is None

    # step.start 应是 run.start 的子 span
    step_parents_ok = bool(step_start_spans) and all(
        parent_id(s) == run_start_span.context.span_id
        for s in step_start_spans
        if run_start_span is not None
    )

    # model.complete / tool.before / tool.after 应是某个 step.start 的子 span
    step_span_ids = {s.context.span_id for s in step_start_spans}
    model_complete_parented = bool(model_complete_spans) and all(
        parent_id(s) in step_span_ids for s in model_complete_spans
    )
    tool_before_parented = bool(tool_before_spans) and all(
        parent_id(s) in step_span_ids for s in tool_before_spans
    )
    tool_after_parented = bool(tool_after_spans) and all(
        parent_id(s) in step_span_ids for s in tool_after_spans
    )

    # 至少存在一个非根 span（证明不再是全扁平）
    has_child_spans = any(parent_id(s) is not None for s in spans)

    parent_child_ok = (
        run_start_is_root
        and step_parents_ok
        and model_complete_parented
        and tool_before_parented
        and tool_after_parented
        and has_child_spans
    )

    # 详细 parent 信息（便于诊断）
    parent_info = []
    for s in spans:
        pid = parent_id(s)
        parent_info.append({
            "span": s.name,
            "span_id": str(s.context.span_id),
            "parent_span_id": str(pid) if pid else None,
        })

    ok = (
        state.status == "done"
        and len(model_complete_spans) == 2
        and has_token_attrs
        and len(tool_before_spans) == 1
        and len(tool_after_spans) == 1
        and len(run_start_spans) == 1
        and len(run_end_spans) == 1
        and args_captured
        and model_complete_has_run_id
        and parent_child_ok
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "otel-real",
        "sdk": "opentelemetry-sdk（真实 SDK + InMemorySpanExporter，非 mock）",
        "span_count": len(spans),
        "span_names": span_names,
        "model_complete_spans": len(model_complete_spans),
        "has_token_attrs": has_token_attrs,
        "tool_before_spans": len(tool_before_spans),
        "tool_after_spans": len(tool_after_spans),
        "run_start_spans": len(run_start_spans),
        "run_end_spans": len(run_end_spans),
        "args_captured": args_captured,
        "model_complete_has_run_id": model_complete_has_run_id,
        "parent_child_ok": parent_child_ok,
        "run_start_is_root": run_start_is_root,
        "step_parents_ok": step_parents_ok,
        "model_complete_parented": model_complete_parented,
        "tool_before_parented": tool_before_parented,
        "tool_after_parented": tool_after_parented,
        "has_child_spans": has_child_spans,
        "parent_info": parent_info,
        "ok": ok,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_otel_real()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
