"""内核核心类型。纯标准库，无第三方依赖。"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Union


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None  # 工具消息时为工具名


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema


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


@dataclass
class RunState:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[Message] = field(default_factory=list)
    step: int = 0
    status: str = "running"  # running | done | failed | paused
    answer: str | None = None

    def add(self, role: str, content: str, name: str | None = None) -> None:
        self.messages.append(Message(role, content, name))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunState":
        msgs = [Message(**m) for m in d.pop("messages", [])]
        state = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "messages"})
        state.messages = msgs
        return state
