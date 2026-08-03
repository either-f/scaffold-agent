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


class EffectNotFoundError(KeyError):
    """mark_* 传入了账本里不存在的 effect_id（UPDATE 命中 0 行），
    或试图做非法状态迁移时抛出。继承 KeyError 以匹配 spec 要求的"小专用异常"。"""

    def __init__(self, effect_id: str, detail: str = "") -> None:
        self.effect_id = effect_id
        self.detail = detail
        super().__init__(f"effect {effect_id} {detail}".strip())


class IllegalEffectTransitionError(EffectNotFoundError):
    """状态机非法迁移：例如对已 succeeded 的行再调 mark_executing。
    复用 EffectNotFoundError 的基类层次，让"统一 catch 一个异常"成为可能。"""


# 状态机迁移表：每个状态允许迁往的下一状态集合。
# proposed 由 propose() 用 INSERT OR IGNORE 写入（幂等，不在本表管辖范围）；
# 本表只约束 mark_* 的 UPDATE 目标。
# 终态 succeeded / failed / rejected 没有"允许下一状态"，任何再迁移都是非法的。
_EFFECT_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "executing", "succeeded", "failed", "rejected"},
    "approved": {"executing", "succeeded", "failed", "rejected"},
    "executing": {"succeeded", "failed", "executing"},  # executing->executing 允许：幂等重试 attempt+=1
    "succeeded": set(),
    "failed": {"executing"},  # failed->executing：人工修正账本后允许重试
    "rejected": set(),
    "unknown": set(),
}


def _check_transition(current: str, target: str) -> None:
    """校验 status 迁移是否合法。不合法抛 IllegalEffectTransitionError。
    注意：调用方在 _transition 里已经先确认 effect_id 存在（SELECT 命中），
    所以这里不重复 effect_id 维度的检查；错误信息只描述状态迁移本身。"""
    allowed = _EFFECT_TRANSITIONS.get(current)
    if allowed is None:
        raise IllegalEffectTransitionError(
            "<unknown-effect>",
            f"未知当前状态 {current!r}，不允许迁移到 {target!r}",
        )
    if target not in allowed:
        raise IllegalEffectTransitionError(
            "<unknown-effect>",
            f"非法状态迁移 {current!r} -> {target!r}（允许: {sorted(allowed) or '∅'}）",
        )


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
        self._transition(effect_id, "approved")

    def mark_executing(self, effect_id: str) -> None:
        # mark_executing 有自己的 UPDATE（attempt += 1），不能走 _set_status 的通用路径。
        self._transition(effect_id, "executing", bump_attempt=True)

    def mark_succeeded(self, effect_id: str, result_ref: str) -> None:
        self._transition(effect_id, "succeeded", result_ref=result_ref)

    def mark_failed(self, effect_id: str, result_ref: str) -> None:
        self._transition(effect_id, "failed", result_ref=result_ref)

    def mark_rejected(self, effect_id: str) -> None:
        self._transition(effect_id, "rejected")

    def _transition(
        self,
        effect_id: str,
        target: str,
        result_ref: str | None = None,
        bump_attempt: bool = False,
    ) -> None:
        with self._conn:
            # 先读当前状态，做状态机校验，再 UPDATE，全程在同一事务里。
            row = self._conn.execute(
                "SELECT status FROM effects WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            if row is None:
                raise EffectNotFoundError(effect_id, "在账本中不存在")
            current = row[0]
            _check_transition(current, target)

            if bump_attempt:
                cur = self._conn.execute(
                    "UPDATE effects SET status='executing', attempt = attempt + 1, "
                    "result_ref = COALESCE(?, result_ref), updated_ts = ? WHERE effect_id = ?",
                    (result_ref, time.time(), effect_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE effects SET status = ?, result_ref = COALESCE(?, result_ref), "
                    "updated_ts = ? WHERE effect_id = ?",
                    (target, result_ref, time.time(), effect_id),
                )
            if cur.rowcount == 0:
                # 极端情况：事务内被并发删除。按"未知 effect_id"处理。
                raise EffectNotFoundError(effect_id, "UPDATE 命中 0 行（可能在事务中被删除）")

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
