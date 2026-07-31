"""M5 真实 OpenTelemetry 观测验证：用真实 opentelemetry-sdk（非 mock）生成 span，
断言真实 span 数据，而不是只验证 handler 被调用。
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

    from agent_kernel.adapters.model_fake import FakeScriptedModel
    from agent_kernel.adapters.observability import ObservedModel, OtelExporter
    from agent_kernel.adapters.tools_local import LocalToolbox, safe_calc
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

    has_token_attrs = bool(model_complete_spans) and all(
        "prompt_tokens" in s.attributes and "duration_ms" in s.attributes
        for s in model_complete_spans
    )
    # tool.before 的 args 是 dict，OTel 原生只接受标量/同类序列；验证 _otel_attributes
    # 把它序列化成了 JSON 字符串塞进 span，而不是被 SDK 静默丢弃
    tool_before_args = tool_before_spans[0].attributes.get("args", "") if tool_before_spans else ""
    args_captured = '"expression"' in tool_before_args and "2+2" in tool_before_args

    ok = (
        state.status == "done"
        and len(model_complete_spans) == 2
        and has_token_attrs
        and len(tool_before_spans) == 1
        and len(tool_after_spans) == 1
        and len(run_start_spans) == 1
        and len(run_end_spans) == 1
        and args_captured
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
