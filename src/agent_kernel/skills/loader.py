"""SKILL.md 加载器：采用 Anthropic Agent Skills 的目录规范。

渐进披露：list_skills() 只返回 name+description（注入系统提示的就这些）；
load(name) 才读取正文。新增能力 = 往 skills_library/ 放一个文件夹，零代码。
"""
from __future__ import annotations

from pathlib import Path

from ..ports import SkillLoaderPort, SkillMeta


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        return {}, text
    meta: dict[str, str] = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


class DirSkillLoader(SkillLoaderPort):
    def __init__(self, root: str = "skills_library") -> None:
        self.root = Path(root)

    def list_skills(self) -> list[SkillMeta]:
        skills: list[SkillMeta] = []
        if not self.root.exists():
            return skills
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            meta, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            skills.append(
                SkillMeta(
                    name=meta.get("name", skill_md.parent.name),
                    description=meta.get("description", ""),
                    path=str(skill_md),
                )
            )
        return skills

    def load(self, name: str) -> str:
        for s in self.list_skills():
            if s.name == name:
                _, body = _parse_frontmatter(Path(s.path).read_text(encoding="utf-8"))
                return body
        raise KeyError(f"未知技能: {name}")
