"""SQLite 记忆 adapter：接口形态对齐 Mem0（add/search）。

当前检索为关键词 LIKE（占位实现）；M3 换 pgvector 语义检索 adapter，接口不变——
这正是 MemoryPort 存在的意义。
"""
from __future__ import annotations

import sqlite3
import time

from ...ports import MemoryPort


class SqliteMemory(MemoryPort):
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            "id INTEGER PRIMARY KEY, run_id TEXT, role TEXT, content TEXT, ts REAL, "
            "importance REAL NOT NULL DEFAULT 1.0, expires_at REAL)"
        )

    def add(
        self,
        run_id: str,
        role: str,
        content: str,
        importance: float = 1.0,
        ttl_seconds: float | None = None,
    ) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        self.conn.execute(
            "INSERT INTO memories(run_id, role, content, ts, importance, expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (run_id, role, content, time.time(), importance, expires_at),
        )
        self.conn.commit()

    def search(self, query: str, k: int = 5) -> list[str]:
        # 占位：取 query 里最长的词做 LIKE；语义检索见 M3 pgvector adapter
        words = sorted(query.split(), key=len, reverse=True)
        if not words:
            return []
        rows = self.conn.execute(
            "SELECT content FROM memories WHERE content LIKE ? "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY importance DESC, ts DESC LIMIT ?",
            (f"%{words[0]}%", time.time(), k),
        ).fetchall()
        return [r[0] for r in rows]

    def prune_expired(self) -> int:
        """TTL 生命周期管理：删除已过期条目，返回删除行数。调用方自行决定巡检节奏
        （离线巩固脚本每次运行前调一次即可，内核不主动调用——见 add() 的 ttl_seconds）。"""
        cur = self.conn.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount
