"""Demo 2: 安全文件整理与技能发现 — 本地工具链 + 渐进式技能披露。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_sqlite import SqliteMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.adapters.tools_skills import SkillToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader

SKILL_MD = """---
name: file-ops
description: 安全文件组织、分类与批量重命名
---

# File Operations Skill

## 流程
1. 列出目标目录文件清单，按扩展名分组。
2. 对每组文件计算数量与总大小。
3. 输出组织结构建议（如按日期/类型分文件夹）。
"""


def event_bus() -> EventBus:
    bus = EventBus()
    bus.subscribe("*", lambda e: print(f"[event] {e.type:14s} {e.payload}"))
    return bus


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        skills_root = Path(tmp) / "skills_library"
        d = skills_root / "file-ops"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

        test_dir = Path(tmp) / "project_files"
        test_dir.mkdir()
        for name, size in [("report.txt", 150), ("data.csv", 420), ("notes.md", 80)]:
            (test_dir / name).write_text("x" * size)

        loader = DirSkillLoader(str(skills_root))
        inner = LocalToolbox()
        inner.register("now", "获取当前日期时间", lambda: __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        inner.register("calc", "计算表达式", lambda expression: str(eval(expression, {"__builtins__": {}}, {})))

        STEPS = [
            '{"thought": "先发现可用技能，看看文件操作技能是否可用", "tool": "load_skill", "args": {"name": "file-ops"}}',
            '{"thought": "技能加载完成，需要获取时间戳标记文件组织时间", "tool": "now", "args": {}}',
            '{"thought": "现在模拟文件组织：计算3个文件(420+150+80)的总大小", "tool": "calc", "args": {"expression": "420+150+80"}}',
            '{"thought": "文件整理完成：3个文件共650字节，按扩展名分为 txt/csv/md 三组。已加载 file-ops 技能流程。", "final": "文件整理完成。目录 project_files 包含 3 个文件，总大小 650 字节。按扩展名分为 txt/report.txt、csv/data.csv、md/notes.md 三组。建议按类型创建子文件夹归档。技能 file-ops 已加载并应用。"}',
        ]

        kernel = AgentKernel(
            model=FakeScriptedModel(STEPS),
            tools=SkillToolbox(inner, loader),
            planner=ReactPlanner(),
            memory=SqliteMemory(),
            bus=event_bus(),
            max_steps=6,
        )

        print(f"可用技能: {[s.name for s in loader.list_skills()]}")
        print(f"可用工具: {[s.name for s in kernel.tools.list_tools()]}")
        print()

        state = kernel.run(
            f"请整理项目文件：目录 {test_dir} 中包含 txt/csv/md 文件，"
            f"先发现可用技能，再按技能规范组织文件，最后报告整理结果。"
        )

        print()
        print(f"状态: {state.status}, 步数: {state.step}")
        print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and "650" in (state.answer or "") else 1


if __name__ == "__main__":
    raise SystemExit(main())
