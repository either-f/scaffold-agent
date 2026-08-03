"""JSON 文件版 CheckpointStore：每步一份快照 + latest 指针。

M3 起可新增 sqlite/Postgres adapter，接口不变。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ports import CheckpointStore
from .types import RunState


class CheckpointConflictError(RuntimeError):
    """乐观并发控制冲突：磁盘上的 latest.json 比内存 state.revision 更新，
    说明有另一个 writer 写过更新的 checkpoint。SPEC-86。

    不加文件锁——这是 CAS-lite 检测，不是锁；读-比-写窗口理论上仍有竞态，
    但把常见的"同一旧状态 resume 两次"从静默丢数据变成可检测错误。"""

    def __init__(self, run_id: str, on_disk_revision: int, expected_revision: int) -> None:
        self.run_id = run_id
        self.on_disk_revision = on_disk_revision
        self.expected_revision = expected_revision
        super().__init__(
            f"checkpoint 冲突：run_id={run_id} 磁盘 revision={on_disk_revision} "
            f"高于内存预期 revision={expected_revision}，疑似并发写或 stale resume"
        )


class JsonCheckpointStore(CheckpointStore):
    def __init__(self, root: str = "runs") -> None:
        self.root = Path(root)

    def save(self, state: RunState) -> None:
        run_dir = self.root / state.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # 乐观并发控制：写前读 latest.json 的 revision，若磁盘版本比内存预期更新，
        # 抛 CheckpointConflictError 而不是静默覆盖。SPEC-86。
        latest = run_dir / "latest.json"
        if latest.exists():
            try:
                on_disk = json.loads(latest.read_text(encoding="utf-8"))
                on_disk_rev = int(on_disk.get("revision", 0))
            except (json.JSONDecodeError, ValueError, OSError):
                on_disk_rev = 0
            # state.revision 是"上一次成功 save 后该有的值"；当前内存 state 还没递增。
            # 磁盘 >= state.revision + 1 说明有别的 writer 写过更新 checkpoint。
            if on_disk_rev > state.revision:
                raise CheckpointConflictError(state.run_id, on_disk_rev, state.revision)

        state.revision += 1
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        turn = state.turn or 1
        self._atomic_write(run_dir / f"turn_{turn:03d}_step_{state.step:03d}.json", data)
        self._atomic_write(run_dir / "latest.json", data)

    def load(self, run_id: str) -> RunState | None:
        latest = self.root / run_id / "latest.json"
        if not latest.exists():
            return None
        return RunState.from_dict(json.loads(latest.read_text(encoding="utf-8")))

    def load_step(self, run_id: str, turn: int, step: int) -> RunState | None:
        """读任意历史 (turn, step) 快照，不止 latest.json。给 fork() 用（ADR-0009）。"""
        path = self.root / run_id / f"turn_{turn:03d}_step_{step:03d}.json"
        if not path.exists():
            return None
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
