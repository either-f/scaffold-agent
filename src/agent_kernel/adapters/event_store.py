"""SQLite 事件账本 adapter（ADR-0009）。风格照抄 adapters/effects.py。

纯 EventBus 订阅者，不是 port（内核从不依赖它）。默认以 best-effort 注册
（``critical=False``），跟 JsonlEventRecorder 同一可靠性等级；需要把事件账本
提升为"正确性必须可见"的事实来源时，调用方显式
``bus.subscribe("*", store.handler(), critical=True)``。

SPEC-94: 持久化 ``event_id`` / ``schema_version`` / ``sequence`` 三列。
旧库（SPEC-94 前创建）自动 ``ALTER TABLE ADD COLUMN``，旧行用默认值回填，
读取时 ``event_id=""`` / ``schema_version=1`` / ``sequence=0`` 可接受。
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
        # SPEC-94: 增量加列，兼容旧库。SQLite ALTER TABLE ADD COLUMN 带默认值会回填旧行。
        self._ensure_column("event_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("schema_version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("sequence", "INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()

    def _ensure_column(self, name: str, decl: str) -> None:
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(events)").fetchall()}
        if name not in cols:
            self._conn.execute(f"ALTER TABLE events ADD COLUMN {name} {decl}")

    def handler(self) -> Handler:
        def _handle(event: Event) -> None:
            run_id = str(event.payload.get("run_id", ""))
            with self._conn:
                self._conn.execute(
                    "INSERT INTO events (run_id, type, payload, ts, event_id, schema_version, sequence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        event.type,
                        json.dumps(event.payload, ensure_ascii=False, default=str),
                        event.ts,
                        event.event_id,
                        event.schema_version,
                        event.sequence,
                    ),
                )

        return _handle

    def load_events(self, run_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT type, payload, ts, event_id, schema_version, sequence "
            "FROM events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [
            Event(
                type=row[0],
                payload=json.loads(row[1]),
                ts=row[2],
                event_id=row[3],
                schema_version=row[4],
                sequence=row[5],
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteEventStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
