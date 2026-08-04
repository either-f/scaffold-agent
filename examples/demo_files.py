r"""Demo 2: 安全文件审查 --- 真实本地工具 + 渐进式技能披露。

模型通过真正的 `list_dir` 工具读取临时目录中的文件名与大小，
基于工具返回的真实数据给出整理建议（只读审查，不修改文件）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory.sqlite import SqliteMemory
from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.tools.local import LocalToolbox
from agent_kernel.adapters.tools.skills import SkillToolbox
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader

SKILL_MD = """---
name: file-ops
description: 安全文件审查、分类与整理建议
---

# File Operations Skill

## 流程
1. 使用 list_dir 工具列出目标目录中所有文件的名称与大小。
2. 按扩展名分组，统计每组文件数与总大小。
3. 输出只读审查结果：按类型分类建议（不修改任何文件）。
"""


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

        allowed_root = test_dir.resolve()

        def list_dir(dir_path: str) -> str:
            """列出目录中所有文件及其大小（只读，不修改任何文件）。"""
            resolved = Path(dir_path).resolve()
            if not resolved.is_relative_to(allowed_root):
                raise PermissionError(f"拒绝访问: {dir_path} (路径不在允许范围内)")
            if not resolved.is_dir():
                return f"错误: {dir_path} 不是有效目录"
            entries = []
            total = 0
            for child in sorted(resolved.iterdir()):
                if child.is_file():
                    sz = child.stat().st_size
                    entries.append(f"{child.name}: {sz} bytes")
                    total += sz
            entries.append(f"--- 总计 {len(entries)-1} 个文件, {total} bytes ---")
            return "\n".join(entries)

        loader = DirSkillLoader(str(skills_root))
        inner = LocalToolbox()
        inner.register(
            "list_dir", "列出目录中所有文件的名称与大小（只读，不修改文件）",
            list_dir,
            parameters={"type": "object", "properties": {"dir_path": {"type": "string"}}, "required": ["dir_path"]},
        )

        STEPS = [
            json.dumps({"thought": "先加载 file-ops 技能了解审查流程", "tool": "load_skill", "args": {"name": "file-ops"}}),
            json.dumps({"thought": "按技能规范第一步：列出目标目录文件", "tool": "list_dir", "args": {"dir_path": str(test_dir)}}),
            json.dumps({"thought": "基于工具返回的真实数据给出分类建议", "final": "文件审查完成（只读，未修改任何文件）。目录包含 3 个文件共 650 bytes。按扩展名分类建议：txt/report.txt、csv/data.csv、md/notes.md 各放一个子文件夹。"}),
        ]

        kernel = AgentKernel(
            model=FakeScriptedModel(list(STEPS)),
            tools=SkillToolbox(inner, loader),
            planner=ReactPlanner(),
            memory=SqliteMemory(),
            bus=EventBus(),
            max_steps=6,
        )

        available = [s.name for s in loader.list_skills()]
        print(f"可用技能: {available}")
        print(f"可用工具: {[s.name for s in kernel.tools.list_tools()]}")
        print()

        # 负向自检：尝试越权访问根外路径应触发 PermissionError
        try:
            escaped = Path(tmp).parent.resolve()
            inner.call("list_dir", {"dir_path": str(escaped)})
            print("FAIL: list_dir 未拒绝越权路径", file=sys.stderr)
            return 1
        except PermissionError:
            pass

        state = kernel.run(
            "请审查项目文件目录，先发现可用技能，再按技能规范使用 list_dir 工具审查文件，最后报告结果。"
        )

        print()
        print(f"状态: {state.status}, 步数: {state.step}")
        print(f"最终答案:\n{state.answer}")

    return 0 if state.status == "done" and "650" in (state.answer or "") else 1


if __name__ == "__main__":
    raise SystemExit(main())
