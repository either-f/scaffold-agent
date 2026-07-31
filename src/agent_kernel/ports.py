"""六个端口 + 横切件的抽象契约。

扩展性规则：任何新概念都实现为某个端口的新 adapter / 新策略；
内核（kernel.py）只依赖本文件的抽象，禁止 import 任何第三方包。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .types import Action, Effect, Message, ModelOutput, RunState, ToolResult, ToolSpec


class ModelPort(ABC):
    """模型端口。换模型/厂商 = 换 adapter（如 LiteLLM）。"""

    @abstractmethod
    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput: ...


class ToolPort(ABC):
    """工具端口。本地函数工具与 MCP 工具实现同一接口。"""

    @abstractmethod
    def list_tools(self) -> list[ToolSpec]: ...

    @abstractmethod
    def call(self, name: str, args: dict) -> ToolResult: ...


class MemoryPort(ABC):
    """记忆端口。接口形态对齐 Mem0（add/search），便于日后换图谱/分层记忆。"""

    @abstractmethod
    def add(self, run_id: str, role: str, content: str) -> None: ...

    @abstractmethod
    def search(self, query: str, k: int = 5) -> list[str]: ...


class PlannerPort(ABC):
    """编排策略端口。ReAct / Plan-Execute / CodeAct / handoff 都是可替换策略。"""

    @abstractmethod
    def step(
        self,
        state: RunState,
        model: ModelPort,
        tools: ToolPort,
        memory: MemoryPort | None,
    ) -> Action: ...


@dataclass
class SkillMeta:
    name: str
    description: str
    path: str


class SkillLoaderPort(ABC):
    """技能端口。渐进披露：默认只注入元信息，正文按需加载。"""

    @abstractmethod
    def list_skills(self) -> list[SkillMeta]: ...

    @abstractmethod
    def load(self, name: str) -> str: ...


class CheckpointStore(ABC):
    """横切件：每步落盘，支持断点恢复与回放。语义对齐 LangGraph checkpointer。"""

    @abstractmethod
    def save(self, state: RunState) -> None: ...

    @abstractmethod
    def load(self, run_id: str) -> RunState | None: ...


class EffectLedger(ABC):
    """横切件：工具副作用账本。恢复时先查这个再决定回放/重试/拒绝，
    而不是无脑重新执行 pending_tool（见 ADR-0008）。"""

    @abstractmethod
    def propose(self, effect: Effect) -> None: ...

    @abstractmethod
    def mark_approved(self, effect_id: str) -> None: ...

    @abstractmethod
    def mark_executing(self, effect_id: str) -> None: ...  # 每次调用 attempt += 1

    @abstractmethod
    def mark_succeeded(self, effect_id: str, result_ref: str) -> None: ...

    @abstractmethod
    def mark_failed(self, effect_id: str, result_ref: str) -> None: ...

    @abstractmethod
    def get(self, effect_id: str) -> Effect | None: ...


class InteropPort(ABC):
    """互操作端口（M6 落地）：以 A2A Agent Card 形式对外暴露本 agent。"""

    @abstractmethod
    def agent_card(self) -> dict: ...

    @abstractmethod
    def handle_task(self, task: dict) -> dict: ...
