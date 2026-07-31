"""观测与成本适配器（M5 / SPEC-51）。

把 EventBus 上的事件转成持久化与外部导出：
- ObservedModel：包装任意 ModelPort，发布 model.complete（含耗时与 token 用量）。
- JsonlEventRecorder：通配订阅，把事件按顺序写成 JSONL，用于离线回放。
- CostLedger：标准库 SQLite 成本台账，按 run 聚合 token 与费用。
- OtelExporter / LangfuseExporter：可选 SDK 的惰性导出适配器，失败不影响 run。

内核零依赖；OTel/Langfuse 只在构造对应 exporter 时才 import。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..events import EventBus, Handler
from ..ports import ModelPort
from ..types import Event, Message, ModelOutput, ToolSpec


class ObservedModel(ModelPort):
    """包装任意 ModelPort，在每次 complete 后发布 model.complete 事件。"""

    def __init__(self, model: ModelPort, bus: EventBus) -> None:
        self.model = model
        self.bus = bus

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        started = time.perf_counter()
        output = self.model.complete(messages, tools)
        duration_ms = (time.perf_counter() - started) * 1000
        usage = output.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        self.bus.publish(
            Event(
                "model.complete",
                {
                    "duration_ms": duration_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )
        )
        return output


class JsonlEventRecorder:
    """通配订阅者：把全部事件按 publish 顺序追加成 JSONL。

    每行一个事件对象：{"ts": ..., "type": ..., "payload": ...}。
    只负责记录；回放解析由调用方完成。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def handler(self) -> Handler:
        def _record(event: Event) -> None:
            line = json.dumps(
                {"ts": event.ts, "type": event.type, "payload": event.payload},
                ensure_ascii=False,
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        return _record


class CostLedger:
    """SQLite 成本台账：订阅 model.complete / run.start，按 run 聚合 token 与费用。

    价格单位为「美元 / 百万 token」。run_id 关联规则：
    - 最近一次 run.start 的 run_id 视为当前活跃 run。
    - model.complete 事件若自带 run_id 则用之，否则落到当前活跃 run。
    - 无 run_id 且无活跃 run 的事件会被忽略。

    ponytail: 单活跃 run 关联是刻意简化；多并发 run 会互相覆盖，
    届时需要事件总线统一携带 run_id 或引入显式 trace 上下文。
    """

    def __init__(
        self,
        path: str | Path,
        prompt_price_per_million: float = 0.0,
        completion_price_per_million: float = 0.0,
    ) -> None:
        for name, price in (
            ("prompt_price_per_million", prompt_price_per_million),
            ("completion_price_per_million", completion_price_per_million),
        ):
            if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
                raise ValueError(f"{name} 必须是非负数字")
        self.prompt_price = float(prompt_price_per_million)
        self.completion_price = float(completion_price_per_million)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_costs (
                run_id TEXT PRIMARY KEY,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        self._active_run_id: str | None = None

    def handler(self) -> Handler:
        def _handle(event: Event) -> None:
            if event.type == "run.start":
                run_id = event.payload.get("run_id")
                if run_id:
                    self._active_run_id = str(run_id)
            elif event.type == "model.complete":
                self._record_model_complete(event)

        return _handle

    def _record_model_complete(self, event: Event) -> None:
        payload = event.payload
        run_id = payload.get("run_id") or self._active_run_id
        if not run_id:
            return
        prompt_tokens = int(payload.get("prompt_tokens", 0))
        completion_tokens = int(payload.get("completion_tokens", 0))
        cost = (prompt_tokens * self.prompt_price + completion_tokens * self.completion_price) / 1_000_000
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO run_costs (run_id, prompt_tokens, completion_tokens, cost_usd, updated_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    cost_usd = cost_usd + excluded.cost_usd,
                    updated_ts = excluded.updated_ts
                """,
                (run_id, prompt_tokens, completion_tokens, cost, time.time()),
            )

    def get_run_cost(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT run_id, prompt_tokens, completion_tokens, cost_usd, updated_ts FROM run_costs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return {
                "run_id": run_id,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "updated_ts": None,
            }
        return {
            "run_id": row[0],
            "prompt_tokens": row[1],
            "completion_tokens": row[2],
            "cost_usd": row[3],
            "updated_ts": row[4],
        }

    def totals(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0) FROM run_costs"
        ).fetchone()
        return {
            "prompt_tokens": row[0],
            "completion_tokens": row[1],
            "cost_usd": row[2],
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CostLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@runtime_checkable
class _OtelTracer(Protocol):
    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Any: ...


_OTEL_PRIMITIVE_TYPES = (bool, str, bytes, int, float)


def _otel_attributes(payload: dict[str, Any]) -> dict[str, Any]:
    """OTel span attribute 只接受 bool/str/bytes/int/float 或它们的同类序列；
    其它类型（比如 tool.before 的 args dict）SDK 会静默丢弃该字段，只打一条
    warning，不抛异常——外层 try/except 完全捕不到。这里主动序列化成 JSON
    字符串，避免调用参数这类关键调试信息悄悄从 trace 里消失。
    """
    attributes: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, _OTEL_PRIMITIVE_TYPES):
            attributes[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, _OTEL_PRIMITIVE_TYPES) for item in value
        ):
            attributes[key] = value
        elif value is None:
            attributes[key] = ""
        else:
            attributes[key] = json.dumps(value, ensure_ascii=False, default=str)
    return attributes


class OtelExporter:
    """把事件转成 OpenTelemetry span。tracer 可注入，便于离线测试。"""

    def __init__(self, tracer: _OtelTracer) -> None:
        self.tracer = tracer

    @classmethod
    def from_endpoint(cls, endpoint: str | None = None, service_name: str = "agent-kernel") -> "OtelExporter":
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        except ImportError as exc:
            raise ImportError("需要 OpenTelemetry：uv sync --extra obs") from exc

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            except ImportError as exc:
                raise ImportError("OTLP HTTP exporter 需要 opentelemetry-exporter-otlp-proto-http") from exc
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        else:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        return cls(trace.get_tracer(service_name))

    def handler(self) -> Handler:
        def _handle(event: Event) -> None:
            try:
                span = self.tracer.start_span(event.type, attributes=_otel_attributes(event.payload))
                end = getattr(span, "end", None)
                if callable(end):
                    end()
            except Exception:
                # 观测失败绝不影响 run
                pass

        return _handle


@runtime_checkable
class _LangfuseLike(Protocol):
    def event(self, name: str, metadata: dict[str, Any] | None = None, **kwargs: Any) -> Any: ...


class LangfuseExporter:
    """把事件转成 Langfuse event。client 可注入，便于离线测试。"""

    def __init__(self, client: _LangfuseLike) -> None:
        self.client = client

    @classmethod
    def from_env(cls, host: str | None = None, **kwargs: Any) -> "LangfuseExporter":
        try:
            from langfuse import Langfuse
        except ImportError as exc:
            raise ImportError("需要 langfuse：uv sync --extra obs") from exc
        if host:
            kwargs["host"] = host
        return cls(Langfuse(**kwargs))

    def handler(self) -> Handler:
        def _handle(event: Event) -> None:
            try:
                self.client.event(
                    name=event.type,
                    metadata=dict(event.payload),
                    timestamp=event.ts,
                )
            except Exception:
                # 观测失败绝不影响 run
                pass

        return _handle
