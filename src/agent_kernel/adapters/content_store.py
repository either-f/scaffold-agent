"""通用内容存储：三个模块（招聘/GitHub趋势/AI日报）共用一张表 + jsonb 差异字段。

跟 SqliteMemory / SqliteEffectLedger 同一套模式：sqlite 单文件，零外部服务依赖。
去重键是 (module, source_platform, external_id) 联合唯一，重复抓取直接 INSERT OR IGNORE。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .scrapers import RawItem


@dataclass
class ContentItem:
    id: int
    module: str
    source_platform: str
    external_id: str
    url: str
    title: str
    raw_text: str
    structured: dict[str, Any] = field(default_factory=dict)
    status: str = "new"  # new | reviewed | actioned | rejected | expired
    score: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0


class SqliteContentStore:
    def __init__(self, path: str = ":memory:") -> None:
        # check_same_thread=False: FastAPI 同步路由函数跑在线程池里，同一个 store 实例
        # 会被不同线程调用；sqlite3 默认按创建线程校验会直接报错，见 API 层单测。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS content_items("
            "id INTEGER PRIMARY KEY, module TEXT NOT NULL, source_platform TEXT NOT NULL, "
            "external_id TEXT NOT NULL, url TEXT, title TEXT, raw_text TEXT, "
            "structured TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'new', "
            "score REAL NOT NULL DEFAULT 0.0, created_at REAL, updated_at REAL, "
            "UNIQUE(module, source_platform, external_id))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS actions_log("
            "id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "payload TEXT NOT NULL DEFAULT '{}', run_id TEXT, created_at REAL)"
        )
        self.conn.commit()

    def upsert(self, module: str, item: RawItem) -> int | None:
        """插入一条新内容；已存在（去重键命中）则跳过，返回 None。返回新插入行的 id。"""
        now = time.time()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO content_items("
            "module, source_platform, external_id, url, title, raw_text, structured, "
            "score, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                module, item.source_platform, item.external_id, item.url, item.title,
                item.raw_text, json.dumps(item.structured, ensure_ascii=False),
                item.score, now, now,
            ),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def list_items(
        self, module: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[ContentItem]:
        clauses, params = [], []
        if module is not None:
            clauses.append("module = ?")
            params.append(module)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT id, module, source_platform, external_id, url, title, raw_text, "
            f"structured, status, score, created_at, updated_at FROM content_items "
            f"{where} ORDER BY score DESC, created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get(self, item_id: int) -> ContentItem | None:
        row = self.conn.execute(
            "SELECT id, module, source_platform, external_id, url, title, raw_text, "
            "structured, status, score, created_at, updated_at FROM content_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return self._row_to_item(row) if row else None

    def set_status(self, item_id: int, status: str, action: str, run_id: str | None = None,
                    payload: dict[str, Any] | None = None) -> None:
        now = time.time()
        self.conn.execute(
            "UPDATE content_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, item_id),
        )
        self.conn.execute(
            "INSERT INTO actions_log(item_id, action, payload, run_id, created_at) VALUES(?,?,?,?,?)",
            (item_id, action, json.dumps(payload or {}, ensure_ascii=False), run_id, now),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_item(row: tuple) -> ContentItem:
        return ContentItem(
            id=row[0], module=row[1], source_platform=row[2], external_id=row[3],
            url=row[4], title=row[5], raw_text=row[6], structured=json.loads(row[7]),
            status=row[8], score=row[9], created_at=row[10], updated_at=row[11],
        )
