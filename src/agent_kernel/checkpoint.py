"""JSON 文件版 CheckpointStore：每步一份快照 + latest 指针。

M3 起可新增 sqlite/Postgres adapter，接口不变。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ports import CheckpointStore
from .types import RunState


class JsonCheckpointStore(CheckpointStore):
    def __init__(self, root: str = "runs") -> None:
        self.root = Path(root)

    def save(self, state: RunState) -> None:
        run_dir = self.root / state.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        self._atomic_write(run_dir / f"step_{state.step:03d}.json", data)
        self._atomic_write(run_dir / "latest.json", data)

    def load(self, run_id: str) -> RunState | None:
        latest = self.root / run_id / "latest.json"
        if not latest.exists():
            return None
        return RunState.from_dict(json.loads(latest.read_text(encoding="utf-8")))

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
