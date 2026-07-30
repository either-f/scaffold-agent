"""M5 / SPEC-51: 事件观测、JSONL 回放、成本台账、可选 exporter 注入测试。

覆盖：
- ObservedModel 包装后发布 model.complete（耗时 + token 用量），返回原输出。
- JsonlEventRecorder 按顺序记录全部事件，可逐条回放。
- CostLedger 聚合 token/cost，非法价格 fail-closed。
- OtelExporter / LangfuseExporter 使用注入 client 且故障不外溢。
- EventBus 订阅者异常不影响内核与其他订阅者。

运行：PYTHONPATH=src python3 tests/test_m5.py   （也兼容 pytest）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.observability import (
    CostLedger,
    JsonlEventRecorder,
    LangfuseExporter,
    ObservedModel,
    OtelExporter,
)
from agent_kernel.adapters.tools_local import default_toolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.langgraph import LangGraphPlanner
from agent_kernel.planners.plan_execute import PlanExecutePlanner
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.types import Event, Message, ModelOutput, RunState

SCRIPT = [
    '{"thought": "t", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
    '{"thought": "t", "final": "结果是 49"}',
]


def _run_kernel(bus: EventBus | None = None, model: FakeScriptedModel | None = None):
    model = model or FakeScriptedModel(list(SCRIPT))
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=ReactPlanner(),
        bus=bus,
    )
    return kernel.run("算一下 (3+4)*7"), kernel


def test_observed_model_publishes_model_complete():
    bus = EventBus()
    events = []
    bus.subscribe("model.complete", events.append)

    raw = FakeScriptedModel(list(SCRIPT))
    observed = ObservedModel(raw, bus)
    state, _ = _run_kernel(bus=bus, model=observed)

    assert state.status == "done"
    assert raw.calls == 2, "被包装模型应被调用两次"
    assert len(events) == 2, "每次 complete 发布一个 model.complete"
    for event in events:
        assert event.type == "model.complete"
        assert "duration_ms" in event.payload
        assert isinstance(event.payload["duration_ms"], (int, float))
        assert event.payload["duration_ms"] >= 0
        assert event.payload["prompt_tokens"] == 0
        assert event.payload["completion_tokens"] == 0

    # 返回原输出：包装不改变 ModelOutput 内容
    direct = FakeScriptedModel(list(SCRIPT))
    expected = direct.complete([Message("user", "hi")], [])
    wrapped_bus = EventBus()
    observed2 = ObservedModel(FakeScriptedModel(list(SCRIPT)), wrapped_bus)
    actual = observed2.complete([Message("user", "hi")], [])
    assert actual.text == expected.text
    assert actual.usage == expected.usage


def test_jsonl_recorder_replays_run_events_in_order():
    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "trace.jsonl"
        bus = EventBus()
        recorder = JsonlEventRecorder(trace_path)
        bus.subscribe("*", recorder.handler())

        state, _ = _run_kernel(bus=bus)
        run_id = state.run_id

        lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]

        # 关键事件顺序：run.start → step.start → tool.before → tool.after → run.end
        types = [e["type"] for e in events]
        assert types[0] == "run.start"
        assert types[-1] == "run.end"
        assert "step.start" in types
        assert "tool.before" in types
        assert "tool.after" in types

        start_idx = types.index("run.start")
        step_idx = types.index("step.start")
        before_idx = types.index("tool.before")
        after_idx = types.index("tool.after")
        end_idx = len(types) - 1
        assert start_idx < step_idx < before_idx < after_idx < end_idx

        # 所有事件都有 ts / type / payload，可完整回放
        for e in events:
            assert isinstance(e["ts"], (int, float))
            assert isinstance(e["type"], str)
            assert isinstance(e["payload"], dict)
            assert e["payload"].get("run_id") == run_id

        # run.end 的 payload 含最终状态
        end_event = events[-1]
        assert end_event["type"] == "run.end"
        assert end_event["payload"]["status"] == "done"
        assert "49" in end_event["payload"]["answer"]


def test_cost_ledger_accumulates_and_queries_cost():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "costs.db"
        bus = EventBus()
        with CostLedger(
            db_path,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
        ) as ledger:
            bus.subscribe("*", ledger.handler())

            # 模拟 run.start + model.complete 事件
            run_id = "test-run-001"
            bus.publish(Event("run.start", {"run_id": run_id, "input": "hi"}))
            bus.publish(
                Event(
                    "model.complete",
                    {
                        "run_id": run_id,
                        "prompt_tokens": 500_000,
                        "completion_tokens": 250_000,
                    },
                )
            )
            bus.publish(
                Event(
                    "model.complete",
                    {
                        "run_id": run_id,
                        "prompt_tokens": 500_000,
                        "completion_tokens": 250_000,
                    },
                )
            )

            cost = ledger.get_run_cost(run_id)
            assert cost["run_id"] == run_id
            assert cost["prompt_tokens"] == 1_000_000
            assert cost["completion_tokens"] == 500_000
            expected_cost = (1_000_000 * 1.0 + 500_000 * 2.0) / 1_000_000
            assert abs(cost["cost_usd"] - expected_cost) < 1e-9

            totals = ledger.totals()
            assert totals["prompt_tokens"] == 1_000_000
            assert totals["completion_tokens"] == 500_000
            assert abs(totals["cost_usd"] - expected_cost) < 1e-9

        # 重新打开数据库，成本仍持久化
        with CostLedger(db_path, 1.0, 2.0) as ledger2:
            cost2 = ledger2.get_run_cost(run_id)
            assert cost2["prompt_tokens"] == 1_000_000
            assert abs(cost2["cost_usd"] - expected_cost) < 1e-9


def test_cost_ledger_falls_back_to_active_run():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "costs.db"
        with CostLedger(db_path, 1.0, 1.0) as ledger:
            # model.complete 没有 run_id，落到最近 run.start 的 run_id
            ledger.handler()(Event("run.start", {"run_id": "active-run", "input": "hi"}))
            ledger.handler()(Event("model.complete", {"prompt_tokens": 100, "completion_tokens": 50}))
            cost = ledger.get_run_cost("active-run")
            assert cost["prompt_tokens"] == 100
            assert cost["completion_tokens"] == 50

            # 无 run_id 且无活跃 run：忽略，不报错
            ledger2 = CostLedger(Path(tmp) / "costs2.db", 1.0, 1.0)
            ledger2.handler()(Event("model.complete", {"prompt_tokens": 1, "completion_tokens": 1}))
            assert ledger2.totals()["prompt_tokens"] == 0
            ledger2.close()


def test_cost_ledger_rejects_invalid_prices():
    for bad in (-1, "1.5", None, True):
        try:
            CostLedger(Path(tempfile.mkdtemp()) / "x.db", bad, 1.0)
            raise AssertionError(f"非法 prompt 价格应 fail closed: {bad!r}")
        except (ValueError, TypeError):
            pass
        try:
            CostLedger(Path(tempfile.mkdtemp()) / "x.db", 1.0, bad)
            raise AssertionError(f"非法 completion 价格应 fail closed: {bad!r}")
        except (ValueError, TypeError):
            pass


def test_otel_exporter_calls_injected_tracer():
    class FakeSpan:
        def __init__(self, name, attributes):
            self.name = name
            self.attributes = attributes
            self.ended = False

        def end(self):
            self.ended = True

    class FakeTracer:
        def __init__(self):
            self.spans = []

        def start_span(self, name, attributes=None):
            span = FakeSpan(name, attributes)
            self.spans.append(span)
            return span

    tracer = FakeTracer()
    exporter = OtelExporter(tracer)
    bus = EventBus()
    bus.subscribe("*", exporter.handler())

    state, _ = _run_kernel(bus=bus)
    span_names = [s.name for s in tracer.spans]
    assert "run.start" in span_names
    assert "model.complete" not in span_names  # 本测试未包装 ObservedModel
    assert "run.end" in span_names
    for span in tracer.spans:
        assert span.ended


def test_langfuse_exporter_calls_injected_client():
    class FakeLangfuse:
        def __init__(self):
            self.events = []

        def event(self, name, metadata=None, **kwargs):
            self.events.append({"name": name, "metadata": metadata, **kwargs})

    client = FakeLangfuse()
    exporter = LangfuseExporter(client)
    bus = EventBus()
    bus.subscribe("*", exporter.handler())

    state, _ = _run_kernel(bus=bus)
    names = [e["name"] for e in client.events]
    assert "run.start" in names
    assert "run.end" in names
    run_end = next(e for e in client.events if e["name"] == "run.end")
    assert run_end["metadata"]["status"] == "done"


def test_exporter_failure_does_not_break_run():
    class BrokenTracer:
        def start_span(self, name, attributes=None):
            raise RuntimeError("OTel 挂了")

    class BrokenLangfuse:
        def event(self, name, metadata=None, **kwargs):
            raise RuntimeError("Langfuse 挂了")

    bus = EventBus()
    bus.subscribe("*", OtelExporter(BrokenTracer()).handler())
    bus.subscribe("*", LangfuseExporter(BrokenLangfuse()).handler())

    state, _ = _run_kernel(bus=bus)
    assert state.status == "done"
    assert "49" in (state.answer or "")


def test_subscriber_failure_isolated_from_kernel_and_other_subscribers():
    bus = EventBus()
    good_events = []

    def bad_handler(event: Event) -> None:
        raise RuntimeError("订阅者炸了")

    def good_handler(event: Event) -> None:
        good_events.append(event)

    bus.subscribe("*", bad_handler)
    bus.subscribe("*", good_handler)

    state, _ = _run_kernel(bus=bus)
    assert state.status == "done"
    assert len(good_events) > 0, "好订阅者仍收到事件"
    assert any(e.type == "run.end" for e in good_events)


def test_event_bus_wildcard_and_specific_subscribers_both_fire():
    bus = EventBus()
    wildcard = []
    specific = []
    bus.subscribe("*", wildcard.append)
    bus.subscribe("run.start", specific.append)

    state, _ = _run_kernel(bus=bus)
    assert len(specific) == 1
    assert specific[0].type == "run.start"
    assert len(wildcard) >= len(specific)


# -------------------------------------------------- SPEC-52A: Plan-Execute & LangGraph planners

PLAN_SCRIPT = [
    '{"thought": "先计划再算", "plan": "1. 用 calc 计算 (3+4)*7", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
    '{"thought": "结果是 49", "final": "结果是 49"}',
]


def test_plan_execute_creates_plan_then_executes():
    """首步生成计划并存入 messages，随后步骤复用 ReAct 执行直到 final。"""
    model = FakeScriptedModel(list(PLAN_SCRIPT))
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=PlanExecutePlanner(),
    )
    state = kernel.run("算一下 (3+4)*7")
    assert state.status == "done"
    assert "49" in (state.answer or "")
    assert model.calls == 2, "plan + final 两次 model 调用"
    plan_msgs = [m for m in state.messages if m.role == "assistant" and m.content.startswith("[计划]")]
    assert len(plan_msgs) == 1, "计划应存入 messages 且仅一次"
    assert any(m.role == "tool" and m.content == "49" for m in state.messages)


def test_plan_execute_empty_plan_still_executes():
    """空计划不阻塞执行：计划提取失败时仍继续走动作解析。"""
    script = [
        '{"thought": "没有计划字段", "tool": "calc", "args": {"expression": "2*3"}}',
        '{"thought": "t", "final": "6"}',
    ]
    model = FakeScriptedModel(list(script))
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=PlanExecutePlanner(),
    )
    state = kernel.run("算 2*3")
    assert state.status == "done"
    assert "6" in (state.answer or "")
    plan_msgs = [m for m in state.messages if m.role == "assistant" and m.content.startswith("[计划]")]
    assert len(plan_msgs) == 0, "空计划不应存入 messages"


class _FakeGraph:
    """最小 compiled-graph 替身：按预设队列返回结果。"""

    def __init__(self, outputs: list) -> None:
        self._outputs = list(outputs)
        self.calls = []

    def invoke(self, payload, *args, **kwargs):
        self.calls.append(payload)
        return self._outputs.pop(0) if self._outputs else {}


def test_langgraph_final_branch():
    """图输出 {final: ...} 翻译为 FinalAnswer，内核直接 done。"""
    graph = _FakeGraph([{"final": "图给出的答案", "thought": "完成"}])
    kernel = AgentKernel(
        model=FakeScriptedModel([]),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
    )
    state = kernel.run("hi")
    assert state.status == "done"
    assert state.answer == "图给出的答案"
    assert len(graph.calls) == 1
    assert "tools" in graph.calls[0] and "messages" in graph.calls[0]


def test_langgraph_tool_branch():
    """图输出 {tool, args} 翻译为 ToolCall，工具结果回灌后再调图得 final。"""
    graph = _FakeGraph([
        {"tool": "calc", "args": {"expression": "5*8"}, "thought": "算一下"},
        {"final": "40"},
    ])
    kernel = AgentKernel(
        model=FakeScriptedModel([]),
        tools=default_toolbox(),
        planner=LangGraphPlanner(graph),
    )
    state = kernel.run("算 5*8")
    assert state.status == "done"
    assert state.answer == "40"
    assert any(m.role == "tool" and m.content == "40" for m in state.messages)
    assert len(graph.calls) == 2


def test_langgraph_malformed_output():
    """非 dict / 缺关键字段 / 空值 -> ValueError，内核标 failed。"""
    tools = default_toolbox()
    known = {t.name for t in tools.list_tools()}

    for bad, label in [
        ("not a dict", "非 dict"),
        ([], "非 dict"),
        ({"foo": "bar"}, "缺 final/tool"),
        ({"final": ""}, "空 final"),
        ({"final": "   "}, "空白 final"),
        ({"tool": "calc", "args": "not-dict"}, "args 非 dict"),
    ]:
        graph = _FakeGraph([bad])
        planner = LangGraphPlanner(graph)
        try:
            planner.step(RunState(), FakeScriptedModel([]), tools, None)
            raise AssertionError(f"应拒绝: {label}")
        except ValueError:
            pass


def test_langgraph_unknown_tool():
    """图返回未知工具名 -> ValueError。"""
    graph = _FakeGraph([{"tool": "nonexistent", "args": {}, "thought": "t"}])
    planner = LangGraphPlanner(graph)
    try:
        planner.step(RunState(), FakeScriptedModel([]), default_toolbox(), None)
        raise AssertionError("未知工具应拒绝")
    except ValueError as e:
        assert "nonexistent" in str(e)


def test_planners_are_replaceable_plannerport():
    """两个 planner 都是 PlannerPort 实现，可热替换。"""
    from agent_kernel.ports import PlannerPort

    assert issubclass(PlanExecutePlanner, PlannerPort)
    assert issubclass(LangGraphPlanner, PlannerPort)

    model = FakeScriptedModel([
        '{"thought": "t", "plan": "直接答", "final": "pe-ok"}',
    ])
    state = AgentKernel(
        model=model, tools=default_toolbox(), planner=PlanExecutePlanner()
    ).run("test")
    assert state.answer == "pe-ok"

    graph = _FakeGraph([{"final": "lg-ok"}])
    state2 = AgentKernel(
        model=FakeScriptedModel([]), tools=default_toolbox(), planner=LangGraphPlanner(graph)
    ).run("test")
    assert state2.answer == "lg-ok"


if __name__ == "__main__":
    test_observed_model_publishes_model_complete()
    test_jsonl_recorder_replays_run_events_in_order()
    test_cost_ledger_accumulates_and_queries_cost()
    test_cost_ledger_falls_back_to_active_run()
    test_cost_ledger_rejects_invalid_prices()
    test_otel_exporter_calls_injected_tracer()
    test_langfuse_exporter_calls_injected_client()
    test_exporter_failure_does_not_break_run()
    test_subscriber_failure_isolated_from_kernel_and_other_subscribers()
    test_event_bus_wildcard_and_specific_subscribers_both_fire()
    test_plan_execute_creates_plan_then_executes()
    test_plan_execute_empty_plan_still_executes()
    test_langgraph_final_branch()
    test_langgraph_tool_branch()
    test_langgraph_malformed_output()
    test_langgraph_unknown_tool()
    test_planners_are_replaceable_plannerport()
    print("OK: M5 全部测试通过")
