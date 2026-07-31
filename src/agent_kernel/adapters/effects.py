"""SQLite 工具副作用账本 adapter（ADR-0008）。

风格对齐 observability.py 的 CostLedger：本机 sqlite3、__init__ 建表、
每次操作走 `with self._conn:` 事务。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..ports import EffectLedger
from ..types import Effect


class SqliteEffectLedger(EffectLedger):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS effects (
                effect_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT,
                result_ref TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def propose(self, effect: Effect) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO effects
                    (effect_id, run_id, tool_name, arguments_hash, status,
                     idempotency_key, result_ref, attempt, updated_ts)
                VALUES (?, ?, ?, ?, 'proposed', ?, NULL, 0, ?)
                """,
                (
                    effect.effect_id,
                    effect.run_id,
                    effect.tool_name,
                    effect.arguments_hash,
                    effect.idempotency_key,
                    time.time(),
                ),
            )

    def mark_approved(self, effect_id: str) -> None:
        self._set_status(effect_id, "approved")

    def mark_executing(self, effect_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE effects SET status='executing', attempt = attempt + 1, updated_ts = ? WHERE effect_id = ?",
                (time.time(), effect_id),
            )

    def mark_succeeded(self, effect_id: str, result_ref: str) -> None:
        self._set_status(effect_id, "succeeded", result_ref)

    def mark_failed(self, effect_id: str, result_ref: str) -> None:
        self._set_status(effect_id, "failed", result_ref)

    def _set_status(self, effect_id: str, status: str, result_ref: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE effects SET status = ?, result_ref = COALESCE(?, result_ref), updated_ts = ? WHERE effect_id = ?",
                (status, result_ref, time.time(), effect_id),
            )

    def get(self, effect_id: str) -> Effect | None:
        row = self._conn.execute(
            """SELECT effect_id, run_id, tool_name, arguments_hash, status,
                      idempotency_key, result_ref, attempt
               FROM effects WHERE effect_id = ?""",
            (effect_id,),
        ).fetchone()
        if row is None:
            return None
        return Effect(
            effect_id=row[0],
            run_id=row[1],
            tool_name=row[2],
            arguments_hash=row[3],
            status=row[4],
            idempotency_key=row[5],
            result_ref=row[6],
            attempt=row[7],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteEffectLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
