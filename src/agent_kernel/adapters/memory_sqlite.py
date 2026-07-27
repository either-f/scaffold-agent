"""SQLite 记忆 adapter：接口形态对齐 Mem0（add/search）。

当前检索为关键词 LIKE（占位实现）；M3 换 pgvector 语义检索 adapter，接口不变——
这正是 MemoryPort 存在的意义。
"""
from __future__ import annotations

import sqlite3
import time

from ..ports import MemoryPort


class SqliteMemory(MemoryPort):
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            "id INTEGER PRIMARY KEY, run_id TEXT, role TEXT, content TEXT, ts REAL)"
        )

    def add(self, run_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO memories(run_id, role, content, ts) VALUES(?,?,?,?)",
            (run_id, role, content, time.time()),
        )
        self.conn.commit()

    def search(self, query: str, k: int = 5) -> list[str]:
        # 占位：取 query 里最长的词做 LIKE；语义检索见 M3 pgvector adapter
        words = sorted(query.split(), key=len, reverse=True)
        if not words:
            return []
        rows = self.conn.execute(
            "SELECT content FROM memories WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"%{words[0]}%", k),
        ).fetchall()
        return [r[0] for r in rows]
