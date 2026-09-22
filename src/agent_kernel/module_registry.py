"""模块注册表：调度层与前端导航都读这份数据，不硬编码模块列表。

加新模块 = 加一条 ModuleSpec，不改调度器/API/前端代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModuleSpec:
    id: str
    label: str
    schedule: str  # cron 表达式
    skill: str  # skills_library/ 下的目录名
    scrapers: list[str]  # adapters/scrapers/ 下的模块名
    has_approval: bool = False  # 是否存在非幂等风险动作（自动投递/自动回复），前端据此渲染审批入口


MODULES: list[ModuleSpec] = [
    ModuleSpec(
        id="job-hunter",
        label="招聘",
        schedule="0 */4 * * *",
        skill="job-hunter",
        scrapers=["boss", "zhilian", "xhs", "zhihu", "douyin"],
        has_approval=True,
    ),
    ModuleSpec(
        id="github-trending",
        label="项目挖掘",
        schedule="0 9 * * *",
        skill="github-trending",
        scrapers=["ghfind"],
    ),
    ModuleSpec(
        id="ai-daily-news",
        label="AI日报",
        schedule="0 8 * * *",
        skill="ai-daily-news",
        scrapers=["aihot", "x_kol"],
    ),
]


def get(module_id: str) -> ModuleSpec:
    for m in MODULES:
        if m.id == module_id:
            return m
    raise KeyError(module_id)
