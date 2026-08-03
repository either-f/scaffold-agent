"""内核核心类型。纯标准库，无第三方依赖。"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Union

# run_id 校验：允许字母、数字、下划线、点、连字符，长度 1-128。
# 这是个 deny-list 友好的校验：现有测试里的可读 id（"scn-1"、"turn-scope"、
# "hitl-1"、"fork-approve" 等）全部通过，只挡住路径逃逸/注入字符。
# 额外显式拒绝 "." / ".." 作为整段路径成分，防 Path(root) / ".." 拼出父目录。
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def _validate_run_id(run_id: str) -> None:
    """校验 run_id，防恶意/畸形值逃逸 checkpoint 根目录。

    拒绝：路径分隔符、绝对路径片段、null 字节、"." / ".." 整段、空串。
    见 SPEC-86。"""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"run_id 不能为空，收到: {run_id!r}")
    if "\x00" in run_id:
        raise ValueError(f"run_id 含 null 字节: {run_id!r}")
    if "/" in run_id or "\\" in run_id:
        raise ValueError(f"run_id 含路径分隔符: {run_id!r}")
    if run_id in {".", ".."}:
        raise ValueError(f"run_id 不能为路径成分 '.' 或 '..': {run_id!r}")
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(
            f"run_id 只能含字母、数字、下划线、点、连字符，长度 1-128，收到: {run_id!r}"
        )


class MemoryHit(str):
    """一条记忆检索命中。继承 str 以保持与既有字符串消费者（`in`/`==`/
    `casefold()`/`"\n".join(...)` 等）零改动兼容，同时携带 score/source/run_id
    等溯源信息供需要区分命中来源的调用方使用（SPEC-90）。

    - score: 相似度分数；无真实分数的适配器（如关键词 LIKE）置 None，不编造假分数。
    - source: 命中来源标签，如 "episodic" / "semantic" / "graph"。
    - run_id: 写入该命中内容的 run_id（若已知），否则 None。
    """

    __slots__ = ("score", "source", "run_id")

    def __new__(cls, content: str, *, score: float | None = None, source: str = "", run_id: str | None = None) -> "MemoryHit":
        instance = super().__new__(cls, content)
        instance.score = score
        instance.source = source
        instance.run_id = run_id
        return instance

    def __repr__(self) -> str:  # type: ignore[override]
        return f"MemoryHit(content={str(self)!r}, score={self.score!r}, source={self.source!r}, run_id={self.run_id!r})"

    @property
    def content(self) -> str:
        return str(self)


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
    # SPEC-86: 乐观并发控制——每次 save() 递增，写前与 latest.json 比对发现
    # 磁盘版本更高即说明有另一个 writer 写过更新的 checkpoint，抛 CheckpointConflictError。
    revision: int = 0

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)

    def add(
        self,
        role: str,
        content: str,
        name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages.append(Message(role, content, name, tool_call_id, tool_calls))

    # checkpoint on-disk schema 版本历史：
    #   v1: 早期隐式形状（无 schema_version 字段；turn 可能为 0）
    #   v2: 当前形状（显式 schema_version=2 + revision 字段）
    # from_dict 按缺失/老版本号分支做就地兼容修复。新增字段在此 bump 并补分支。
    SCHEMA_VERSION = 2

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = self.SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunState":
        raw = dict(d)
        msgs = [Message(**m) for m in raw.pop("messages", [])]
        pending = raw.pop("pending_tool", None)
        # schema_version 兼容：缺失视为 v1（早期隐式形状）。SPEC-86。
        schema_version = raw.pop("schema_version", 1)
        # 过滤掉未知字段（向前兼容：新字段读老 checkpoint 时安全忽略）。
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        state = cls(**known)
        state.messages = msgs
        state.pending_tool = ToolCall(**pending) if pending else None
        # v1 兼容：早期 checkpoint turn 可能为 0，统一抬到 1。
        # 原 kernel.py::resume 里的 ad hoc shim，SPEC-86 集中到此处一处。
        if schema_version < 2 and state.turn == 0:
            state.turn = 1
        return state
