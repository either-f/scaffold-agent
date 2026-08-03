"""内核核心类型。纯标准库，无第三方依赖。"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Union


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None  # 工具消息时为工具名
    # 原生 tool calling 回路相关：assistant 消息带上它发起的 tool_call（含 id/name/args），
    # 随后对应的 tool 消息带上同一 tool_call_id，供 adapter 还原 provider 协议要求的
    # {"role":"assistant","tool_calls":[...]} + {"role":"tool","tool_call_id":...} 形状。
    # 非 native 路径（文本 JSON / FakeScriptedModel）这两个字段恒为 None，行为不变。
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None  # assistant 侧：[{"id","name","args"}]


@dataclass
class RetryPolicy:
    max_attempts: int = 1  # 只做整数上限，不做退避/抖动


@dataclass
class ToolEffectPolicy:
    read_only: bool = False
    idempotent: bool = False
    compensatable: bool = False  # 目前只记录，内核逻辑不读取
    # 内核 fail-closed：requires_approval=True 且 kernel 未配置 approval 回调时，
    # 在执行前抛 ApprovalRequiredError，不会静默跑未审批的副作用工具。
    requires_approval: bool = False
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    effect_policy: ToolEffectPolicy | None = None


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    # 原生 tool calling：provider 返回的 tool_call id，需在下一轮回灌时原样匹配。
    # 文本 JSON 路径（含 FakeScriptedModel）不产生 id，保持 None。
    call_id: str | None = None


@dataclass
class FinalAnswer:
    content: str
    thought: str = ""


Action = Union[ToolCall, FinalAnswer]


@dataclass
class ModelOutput:
    text: str
    usage: dict[str, int] = field(default_factory=dict)  # prompt/completion tokens
    # 原生 tool calling：模型明确要求调用工具时非空，元素形如 {"name": ..., "args": {...}}。
    # 为空时走 planner 的文本 JSON 解析兜底（未接原生 tool calling 的 adapter，如 FakeScriptedModel）。
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Event:
    type: str  # run.start / step.start / tool.before / tool.after / run.end ...
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


EffectStatus = Literal[
    "proposed", "approved", "executing", "succeeded", "failed", "rejected", "unknown"
]


@dataclass
class Effect:
    """工具副作用账本条目。恢复时先查这个，而不是无脑重跑 pending_tool。"""

    effect_id: str
    run_id: str
    tool_name: str
    arguments_hash: str
    status: EffectStatus = "proposed"
    idempotency_key: str | None = None
    result_ref: str | None = None
    attempt: int = 0


def hash_arguments(args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class ArtifactRef:
    """工具结果里指向外置产物（如 OffloadingToolbox 落盘的文件）的引用。"""

    uri: str
    mime_type: str = "text/plain"
    description: str = ""


@dataclass
class ToolResult:
    """工具调用结果：content 是回灌给模型的文本，artifacts 是附带的结构化产物引用。"""

    content: str
    artifacts: list[ArtifactRef] = field(default_factory=list)


@dataclass
class RunState:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[Message] = field(default_factory=list)
    step: int = 0
    status: str = "running"  # running | done | failed | paused
    answer: str | None = None
    pending_tool: ToolCall | None = None
    pending_effect_id: str | None = None
    forked_from: str | None = None  # "{source_run_id}@{checkpoint_label}"，非 fork 出来的为 None
    turn: int = 0
    context_summary: str = ""
    summarized_message_count: int = 0

    def add(
        self,
        role: str,
        content: str,
        name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages.append(Message(role, content, name, tool_call_id, tool_calls))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunState":
        raw = dict(d)
        msgs = [Message(**m) for m in raw.pop("messages", [])]
        pending = raw.pop("pending_tool", None)
        state = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        state.messages = msgs
        state.pending_tool = ToolCall(**pending) if pending else None
        return state
