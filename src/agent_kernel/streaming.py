"""流式 token 边带（side-channel）：ModelPort adapter 内部逐 token 产出时，
通过 contextvars 广播给外部订阅者，同时仍然把聚合后的完整文本同步返回给
调用方（planner → kernel）——ports.py / kernel.py / EventBus 全程不用感知
"流式"这个概念，checkpoint、事件序列化路径永远只看到最终完整文本。

跟 FoxChat"contextvars 传 stream_queue 绕过 checkpointer 序列化"是同一个
思路：跨越既有同步调用链的旁路数据用 contextvars 传，而不是把 PlannerPort /
ModelPort 的方法签名都改一遍去传一个 sink 参数（DagPlanner 的 ponytail 注释
里提过"接上需要改端口契约，当前不做"——这次改用 contextvars 就是为了绕开
这个代价）。

用法：
    with stream_to(lambda token: print(token, end="", flush=True)):
        kernel.run("...")

不进 stream_to 上下文时 emit_token() 是空操作，零开销、零侵入现有调用方。

ponytail: contextvars 的 Context 默认不跨 `threading.Thread` 传播（新线程拿到
空 Context）。DagPlanner 内部用 ThreadPoolExecutor 并行跑的是 tools.call()，
不是 model.complete()，所以不受影响；但如果以后有 Planner 在子线程里调
model.complete()，该子线程收不到外层 stream_to 注册的 sink——真遇到这个场景
再用 contextvars.copy_context() 显式传递。
"""
from __future__ import annotations

import contextvars
from typing import Callable

from .events import EventBus
from .types import Event

TokenSink = Callable[[str], None]

_sink: "contextvars.ContextVar[TokenSink | None]" = contextvars.ContextVar(
    "agent_kernel_stream_sink", default=None
)


def emit_token(token: str) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(token)


class stream_to:
    """上下文管理器：包住的代码块里 emit_token() 全部转发给 sink。嵌套时内层
    覆盖外层，退出内层后自动还原成外层的 sink（标准 contextvars.Token 语义）。"""

    def __init__(self, sink: TokenSink) -> None:
        self.sink = sink
        self._reset_token: contextvars.Token | None = None

    def __enter__(self) -> "stream_to":
        self._reset_token = _sink.set(self.sink)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._reset_token is not None:
            _sink.reset(self._reset_token)


def stream_to_bus(bus: EventBus, event_type: str = "token.delta") -> stream_to:
    """把 token 旁路桥接到 EventBus——想用 bus.subscribe(...) 统一订阅内核事件
    （tool.before/run.completed 等）跟 token 流，而不是手写 sink 时用这个。

    ponytail: token 事件量级远高于其它内核事件（每个 token 一条），高频订阅者
    （落盘的 exporter 等）自己做节流，这里不限速——EventBus.publish() 本身也没有
    背压，跟其它事件走的是同一条无背压总线，不是这层新引入的问题。
    """
    return stream_to(lambda token: bus.publish(Event(event_type, {"token": token})))
