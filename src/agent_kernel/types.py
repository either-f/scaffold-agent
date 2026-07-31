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


@dataclass
class RetryPolicy:
    max_attempts: int = 1  # 只做整数上限，不做退避/抖动


@dataclass
class ToolEffectPolicy:
    read_only: bool = False
    idempotent: bool = False
    compensatable: bool = False  # 目前只记录，内核逻辑不读取
    requires_approval: bool = False  # 目前只记录；审批仍由 AgentKernel.approval 是否配置决定
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


@dataclass
class FinalAnswer:
    content: str
    thought: str = ""


Action = Union[ToolCall, FinalAnswer]


@dataclass
class ModelOutput:
    text: str
    usage: dict[str, int] = field(default_factory=dict)  # prompt/completion tokens


@dataclass
class Event:
    type: str  # run.start / step.start / tool.before / tool.after / run.end ...
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


EffectStatus = Literal["proposed", "approved", "executing", "succeeded", "failed", "unknown"]


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
class RunState:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[Message] = field(default_factory=list)
    step: int = 0
    status: str = "running"  # running | done | failed | paused
    answer: str | None = None
    pending_tool: ToolCall | None = None
    pending_effect_id: str | None = None
    turn: int = 0
    context_summary: str = ""
    summarized_message_count: int = 0

    def add(self, role: str, content: str, name: str | None = None) -> None:
        self.messages.append(Message(role, content, name))

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
