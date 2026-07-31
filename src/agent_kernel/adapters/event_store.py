"""SQLite 事件账本 adapter（ADR-0009）。风格照抄 adapters/effects.py。

纯 EventBus 订阅者，不是 port（内核从不依赖它）。继承 EventBus.publish 的
`except Exception: pass`：这是 best-effort，不是 exactly-once，跟
JsonlEventRecorder 是同一个可靠性等级。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..events import Handler
from ..types import Event


class SqliteEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)")
        self._conn.commit()

    def handler(self) -> Handler:
        def _handle(event: Event) -> None:
            run_id = str(event.payload.get("run_id", ""))
            with self._conn:
                self._conn.execute(
                    "INSERT INTO events (run_id, type, payload, ts) VALUES (?, ?, ?, ?)",
                    (run_id, event.type, json.dumps(event.payload, ensure_ascii=False), event.ts),
                )

        return _handle

    def load_events(self, run_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT type, payload, ts FROM events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [Event(type=row[0], payload=json.loads(row[1]), ts=row[2]) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteEventStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
