"""采集端口。每个平台一个 adapter，实现同一份 fetch(query) -> list[RawItem] 契约。

跟 ModelPort/MemoryPort 同一套扩展纪律：新平台 = 新 adapter，不改调用方。
第三方依赖（requests/playwright 等）只准出现在具体平台的 adapter 文件里，
本文件保持零第三方依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RawItem:
    """采集到的一条原始条目，落库前的中间形态。"""

    external_id: str  # 平台内唯一 id，去重用（配合 module+source_platform 联合唯一）
    source_platform: str  # 'boss' | 'zhilian' | 'xhs' | 'ghfind' | 'x' ...
    url: str
    title: str
    raw_text: str
    structured: dict[str, Any] = field(default_factory=dict)  # 模块专属字段
    score: float = 0.0


class ScraperPort(Protocol):
    def fetch(self, query: dict[str, Any]) -> list[RawItem]: ...
