"""技能工具箱：ToolPort 装饰器，把 SkillLoaderPort 暴露为渐进式工具。

发现阶段（list_tools）只披露 name+description，绝不包含技能正文；
正文仅在 agent 显式调用 load 工具时经 SkillLoaderPort.load() 返回。
新增技能 = 往 skills_library/ 放一个 SKILL.md 文件夹，零代码变更。
"""
from __future__ import annotations

from ...ports import SkillLoaderPort, ToolPort
from ...types import ToolResult, ToolSpec

LOAD_TOOL_NAME = "load_skill"


class SkillToolbox(ToolPort):
    """包装既有 ToolPort，追加一个命名空间技能加载工具。"""

    def __init__(self, inner: ToolPort, loader: SkillLoaderPort) -> None:
        self._inner = inner
        self._loader = loader

    def list_tools(self) -> list[ToolSpec]:
        skills = self._loader.list_skills()
        catalog = "\n".join(f"- {s.name}: {s.description}" for s in skills) or "(无可用技能)"
        load_spec = ToolSpec(
            name=LOAD_TOOL_NAME,
            description=(
                "按需加载一个技能的完整正文（SKILL.md body）。"
                "发现阶段只列出名称与描述，正文须通过本工具获取。\n"
                f"可用技能：\n{catalog}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能名称，必须是上方列表中的一项",
                        "enum": [s.name for s in skills],
                    }
                },
                "required": ["name"],
            },
        )
        return [*self._inner.list_tools(), load_spec]

    def call(self, name: str, args: dict) -> ToolResult:
        if name != LOAD_TOOL_NAME:
            return self._inner.call(name, args)
        skill_name = str(args.get("name", ""))
        # fail closed：仅允许 loader 当前已知的精确名称，拒绝未知/穿越名
        known = {s.name for s in self._loader.list_skills()}
        if skill_name not in known:
            raise KeyError(f"未知技能: {skill_name}")
        return ToolResult(content=self._loader.load(skill_name))
