"""图状记忆 adapter：SQLite 持久化事实 + 显式 subject/relation/object 边。

标准 MemoryPort.add/search 存查纯文本事实。
额外提供 add_edge / search_edges / get_neighbors 图谱方法。
忽略 tool-role 写入（对齐 PgVectorMemory），namespace 隔离，内容去重。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time

from ..ports import MemoryPort


class GraphMemory(MemoryPort):
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS graph_facts("
            "id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, run_id TEXT, "
            "role TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "ts REAL, UNIQUE(namespace, role, content_hash))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS graph_edges("
            "id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, "
            "subject TEXT NOT NULL, relation TEXT NOT NULL, object TEXT NOT NULL, "
            "edge_hash TEXT NOT NULL, ts REAL, "
            "UNIQUE(namespace, subject, relation, object))"
        )

    # ------------------------------------------------------ MemoryPort contract
    def add(self, run_id: str, role: str, content: str) -> None:
        content = content.strip()
        if role not in ("user", "assistant") or not content:
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO graph_facts(namespace, run_id, role, content, content_hash, ts) "
            "VALUES(?,?,?,?,?,?)",
            ("default", run_id, role, content, digest, time.time()),
        )
        self.conn.commit()

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []
        words = sorted(query.split(), key=len, reverse=True)
        rows = self.conn.execute(
            "SELECT content FROM graph_facts WHERE namespace=? AND content LIKE ? "
            "ORDER BY ts DESC LIMIT ?",
            ("default", f"%{words[0]}%", k),
        ).fetchall()
        return [r[0] for r in rows]

    # --------------------------------------------------------- graph edge API
    def add_edge(
        self,
        namespace: str,
        subject: str,
        relation: str,
        object: str,
    ) -> None:
        namespace = namespace.strip()
        subject = subject.strip()
        relation = relation.strip()
        object = object.strip()
        if not namespace or not subject or not relation or not object:
            raise ValueError("namespace、subject、relation、object 均不能为空")
        raw = f"{namespace}|{subject}|{relation}|{object}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO graph_edges(namespace, subject, relation, object, edge_hash, ts) "
            "VALUES(?,?,?,?,?,?)",
            (namespace, subject, relation, object, digest, time.time()),
        )
        self.conn.commit()

    def search_edges(
        self,
        namespace: str,
        subject: str | None = None,
        relation: str | None = None,
        object: str | None = None,
        k: int = 20,
    ) -> list[tuple[str, str, str]]:
        conditions = ["namespace = ?"]
        params: list = [namespace]
        if subject:
            conditions.append("subject = ?")
            params.append(subject)
        if relation:
            conditions.append("relation = ?")
            params.append(relation)
        if object:
            conditions.append("object = ?")
            params.append(object)
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT subject, relation, object FROM graph_edges WHERE {where} "
            "ORDER BY ts DESC LIMIT ?",
            (*params, k),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_neighbors(
        self,
        namespace: str,
        node: str,
        direction: str = "both",
        k: int = 20,
    ) -> list[tuple[str, str, str, str]]:
        node = node.strip()
        namespace = namespace.strip()
        node = node.strip()
        if not namespace or not node:
            raise ValueError("namespace 和 node 不能为空")
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError("direction 必须为 outgoing / incoming / both")
        results: list[tuple[str, str, str, str]] = []
        if direction in ("outgoing", "both"):
            rows = self.conn.execute(
                "SELECT subject, relation, object FROM graph_edges "
                "WHERE namespace=? AND subject=? ORDER BY ts DESC LIMIT ?",
                (namespace, node, k),
            ).fetchall()
            results.extend((s, r, o, "outgoing") for s, r, o in rows)
        if direction in ("incoming", "both"):
            rows = self.conn.execute(
                "SELECT subject, relation, object FROM graph_edges "
                "WHERE namespace=? AND object=? ORDER BY ts DESC LIMIT ?",
                (namespace, node, k),
            ).fetchall()
            results.extend((s, r, o, "incoming") for s, r, o in rows)
        return results[:k]
