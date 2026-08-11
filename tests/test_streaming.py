"""流式 side-channel 单测：stream_to 订阅/嵌套还原/异常安全、无订阅时 emit_token
空操作、StreamingModelPort 聚合文本并广播每个 token。

运行：PYTHONPATH=src python3 tests/test_streaming.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.model.streaming import StreamChunk, StreamingModelPort, ToolCallDelta
from agent_kernel.events import EventBus
from agent_kernel.streaming import emit_token, stream_to, stream_to_bus


def test_emit_token_without_sink_is_noop():
    emit_token("no subscriber")  # 不该抛异常


def test_stream_to_captures_tokens():
    captured = []
    with stream_to(captured.append):
        emit_token("a")
        emit_token("b")
    assert captured == ["a", "b"]


def test_emit_token_after_exiting_context_stops_reaching_old_sink():
    captured = []
    with stream_to(captured.append):
        emit_token("inside")
    emit_token("outside")
    assert captured == ["inside"]


def test_nested_stream_to_inner_overrides_then_restores_outer():
    outer, inner = [], []
    with stream_to(outer.append):
        emit_token("o1")
        with stream_to(inner.append):
            emit_token("i1")
        emit_token("o2")
    assert outer == ["o1", "o2"]
    assert inner == ["i1"]


def test_stream_to_restores_sink_even_on_exception():
    outer = []
    try:
        with stream_to(outer.append):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    emit_token("after")  # 应该没有活跃 sink 了
    assert outer == []


def test_streaming_model_port_aggregates_and_broadcasts_tokens():
    def token_iter_fn(messages, tools):
        yield "Hel"
        yield "lo"
        yield "!"

    model = StreamingModelPort(token_iter_fn)
    captured = []
    with stream_to(captured.append):
        output = model.complete([], [])
    assert output.text == "Hello!"
    assert captured == ["Hel", "lo", "!"]
    assert output.tool_calls == []


def test_streaming_model_port_works_without_subscriber():
    def token_iter_fn(messages, tools):
        yield "a"
        yield "b"

    model = StreamingModelPort(token_iter_fn)
    output = model.complete([], [])
    assert output.text == "ab"


def test_streaming_model_port_assembles_fragmented_tool_call_arguments():
    def token_iter_fn(messages, tools):
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="call_1", name="get_weather", arguments_fragment=""))
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, arguments_fragment='{"cit'))
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, arguments_fragment='y": "Bei'))
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, arguments_fragment='jing"}'))

    model = StreamingModelPort(token_iter_fn)
    output = model.complete([], [])

    assert output.text == ""
    assert output.tool_calls == [{"name": "get_weather", "args": {"city": "Beijing"}}]


def test_streaming_model_port_handles_multiple_parallel_tool_calls_by_index():
    def token_iter_fn(messages, tools):
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="call_a", name="tool_a", arguments_fragment='{"x": 1}'))
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=1, id="call_b", name="tool_b", arguments_fragment='{"y": 2}'))

    model = StreamingModelPort(token_iter_fn)
    output = model.complete([], [])

    assert output.tool_calls == [
        {"name": "tool_a", "args": {"x": 1}},
        {"name": "tool_b", "args": {"y": 2}},
    ]


def test_streaming_model_port_tool_call_deltas_are_not_broadcast_as_tokens():
    def token_iter_fn(messages, tools):
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, name="t", arguments_fragment="{}"))

    model = StreamingModelPort(token_iter_fn)
    captured = []
    with stream_to(captured.append):
        model.complete([], [])
    assert captured == []  # 结构化片段不广播，emit_token 只广播纯文本


def test_streaming_model_port_malformed_arguments_json_falls_back_to_empty_dict():
    def token_iter_fn(messages, tools):
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, name="t", arguments_fragment="{not valid json"))

    model = StreamingModelPort(token_iter_fn)
    output = model.complete([], [])
    assert output.tool_calls == [{"name": "t", "args": {}}]


def test_streaming_model_port_text_and_tool_call_can_mix_in_same_stream():
    def token_iter_fn(messages, tools):
        yield "thinking..."
        yield StreamChunk(tool_call_delta=ToolCallDelta(index=0, name="t", arguments_fragment='{"a": 1}'))

    model = StreamingModelPort(token_iter_fn)
    output = model.complete([], [])
    assert output.text == "thinking..."
    assert output.tool_calls == [{"name": "t", "args": {"a": 1}}]


def test_stream_to_bus_publishes_token_delta_events():
    bus = EventBus()
    received = []
    bus.subscribe("token.delta", lambda e: received.append(e.payload["token"]))

    with stream_to_bus(bus):
        emit_token("hi")
        emit_token("there")

    assert received == ["hi", "there"]


def test_stream_to_bus_custom_event_type():
    bus = EventBus()
    received = []
    bus.subscribe("custom.token", lambda e: received.append(e.payload["token"]))

    with stream_to_bus(bus, event_type="custom.token"):
        emit_token("x")

    assert received == ["x"]


def test_stream_to_bus_bridges_streaming_model_port():
    bus = EventBus()
    received = []
    bus.subscribe("token.delta", lambda e: received.append(e.payload["token"]))

    def token_iter_fn(messages, tools):
        yield "foo"
        yield "bar"

    model = StreamingModelPort(token_iter_fn)
    with stream_to_bus(bus):
        output = model.complete([], [])

    assert output.text == "foobar"
    assert received == ["foo", "bar"]


if __name__ == "__main__":
    test_emit_token_without_sink_is_noop()
    test_stream_to_captures_tokens()
    test_emit_token_after_exiting_context_stops_reaching_old_sink()
    test_nested_stream_to_inner_overrides_then_restores_outer()
    test_stream_to_restores_sink_even_on_exception()
    test_streaming_model_port_aggregates_and_broadcasts_tokens()
    test_streaming_model_port_works_without_subscriber()
    test_streaming_model_port_assembles_fragmented_tool_call_arguments()
    test_streaming_model_port_handles_multiple_parallel_tool_calls_by_index()
    test_streaming_model_port_tool_call_deltas_are_not_broadcast_as_tokens()
    test_streaming_model_port_malformed_arguments_json_falls_back_to_empty_dict()
    test_streaming_model_port_text_and_tool_call_can_mix_in_same_stream()
    test_stream_to_bus_publishes_token_delta_events()
    test_stream_to_bus_custom_event_type()
    test_stream_to_bus_bridges_streaming_model_port()
    print("OK: streaming 测试全部通过")
