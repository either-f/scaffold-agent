"""把过长工具结果原子外置到内容寻址文本文件。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..ports import ToolPort
from ..types import ArtifactRef, ToolResult, ToolSpec


class OffloadingToolbox(ToolPort):
    def __init__(
        self,
        tools: ToolPort,
        artifact_dir: str = "runs/artifacts",
        max_inline_chars: int = 8000,
        preview_chars: int = 1000,
    ) -> None:
        if max_inline_chars < 1 or preview_chars < 0:
            raise ValueError("max_inline_chars 必须为正数，preview_chars 不能为负数")
        self.tools = tools
        self.artifact_dir = Path(artifact_dir)
        self.max_inline_chars = max_inline_chars
        self.preview_chars = preview_chars

    def list_tools(self) -> list[ToolSpec]:
        return self.tools.list_tools()

    def call(self, name: str, args: dict) -> ToolResult:
        result = self.tools.call(name, args)
        content = result.content
        if len(content) <= self.max_inline_chars:
            return result

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        path = (self.artifact_dir / f"{digest}.txt").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(".txt.tmp")
            try:
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)

        head = content[: self.preview_chars]
        tail = content[-self.preview_chars :] if self.preview_chars else ""
        preview = (
            f"[offloaded] 完整工具结果: {path}\n"
            f"字符数: {len(content)}\n"
            f"开头预览:\n{head}\n"
            f"结尾预览:\n{tail}"
        )
        return ToolResult(
            content=preview,
            artifacts=[*result.artifacts, ArtifactRef(uri=str(path), description=f"{name} 完整结果")],
        )
