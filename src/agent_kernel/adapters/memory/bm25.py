"""BM25 关键词记忆 adapter：不是占位 Fake，是真的 BM25Okapi 算法（k1=1.5, b=0.75
标准默认值），纯 stdlib + SQLite 持久化语料，零外部依赖。

跟 ElasticsearchMemory 的关系：接口形态一致（MemoryPort.add/search），充当它的离线
Fake 对照（跟 GraphMemory 之于 Neo4jGraphMemory 一样），同时自身就是一个能打的轻量
BM25 后端，不是凑数的占位符——真做了词频/文档频率统计，不是 LIKE 化名。

ponytail: search() 时对 namespace 内全部文档现算 BM25 分数，O(namespace 文档数)
全表 scan，没有倒排索引；几千文档量级够用，海量语料换真 Elasticsearch。

父子索引（add_parent_child）：小块检索、大块回灌——child 是从 parent 切出的小片段，
参与 BM25 打分（片段越小词频统计越聚焦，检索精度更高）；parent 是完整上下文块，
不参与打分，只在某个 child 命中时替换该 child 被返回（模型需要完整上下文，不能只给
命中的那一句话）。同一 parent 下多个 child 都命中时，parent 只返回一次。
"""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import time
from collections import Counter

from ...ports import MemoryPort

K1 = 1.5
B = 0.75

_LATIN_RUN = re.compile(r"[A-Za-z0-9]+")
_NON_WORD = re.compile(r"[^\w]", re.UNICODE)


def _cjk_bigrams(s: str) -> list[str]:
    # ponytail: 单字符 n-gram 天然匹配不到语料里的双字 bigram，索性丢弃而不是造一个
    # 永远配不上的 unigram——查询词用单字 CJK 词是这套分词方案的已知天花板，真要支持
    # 换分词器（jieba 等）。
    s = _NON_WORD.sub("", s)
    if len(s) < 2:
        return []
    return [s[i : i + 2] for i in range(len(s) - 1)]


def tokenize(text: str) -> list[str]:
    """拉丁/数字连续片段整词小写化；CJK（及其它无空格文字）片段退化成字符 2-gram——
    复用 evals/run_consolidation.py 里 _shingles() 处理中文无空格分词的同一思路。"""
    tokens: list[str] = []
    cursor = 0
    for m in _LATIN_RUN.finditer(text):
        tokens.extend(_cjk_bigrams(text[cursor : m.start()]))
        tokens.append(m.group().lower())
        cursor = m.end()
    tokens.extend(_cjk_bigrams(text[cursor:]))
    return tokens


def bm25_rank(
    query: str, docs: list[tuple[int, str, float]]
) -> list[tuple[int, str, float]]:
    """docs: [(id, content, importance), ...]；返回按 BM25 分数（乘 importance 加权）
    降序排列、只保留分数 > 0（有词项命中）的 [(id, content, score), ...]。带 id 走一圈
    是为了父子索引查完分数后还能查回它的 parent_id，不能只靠 content 字符串反查
    （多个 child 文本可能重复，字符串反查会撞）。"""
    query_terms = tokenize(query)
    if not query_terms or not docs:
        return []
    doc_tokens = [tokenize(content) for _, content, _ in docs]
    doc_lens = [len(t) for t in doc_tokens]
    avgdl = sum(doc_lens) / len(doc_lens) if doc_lens else 0.0
    n = len(docs)
    df: Counter[str] = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))

    scored: list[tuple[int, str, float]] = []
    for (doc_id, content, importance), tokens, dl in zip(docs, doc_tokens, doc_lens):
        tf = Counter(tokens)
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            n_t = df[term]
            idf = math.log((n - n_t + 0.5) / (n_t + 0.5) + 1)
            denom = freq + K1 * (1 - B + B * (dl / avgdl if avgdl else 1.0))
            score += idf * (freq * (K1 + 1)) / denom
        if score > 0:
            scored.append((doc_id, content, score * importance))
    scored.sort(key=lambda cs: cs[2], reverse=True)
    return scored


class Bm25Memory(MemoryPort):
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS bm25_docs("
            "id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, run_id TEXT, role TEXT NOT NULL, "
            "content TEXT NOT NULL, content_hash TEXT NOT NULL, ts REAL, "
            "importance REAL NOT NULL DEFAULT 1.0, expires_at REAL, "
            "is_leaf INTEGER NOT NULL DEFAULT 1, parent_id INTEGER, "
            "UNIQUE(namespace, role, content_hash))"
        )

    def add(
        self,
        run_id: str,
        role: str,
        content: str,
        importance: float = 1.0,
        ttl_seconds: float | None = None,
    ) -> None:
        content = content.strip()
        if role not in ("user", "assistant") or not content:
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        self.conn.execute(
            "INSERT OR IGNORE INTO bm25_docs"
            "(namespace, run_id, role, content, content_hash, ts, importance, expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("default", run_id, role, content, digest, time.time(), importance, expires_at),
        )
        self.conn.commit()

    def add_parent_child(
        self,
        run_id: str,
        role: str,
        parent_content: str,
        child_contents: list[str],
        importance: float = 1.0,
        ttl_seconds: float | None = None,
    ) -> None:
        """父子索引：parent_content 存成 is_leaf=0（不参与 BM25 打分，只作为命中后的
        回灌内容），child_contents 各自存成 is_leaf=1 指向 parent（参与打分）。"""
        parent_content = parent_content.strip()
        if role not in ("user", "assistant") or not parent_content or not child_contents:
            return
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        parent_digest = hashlib.sha256(parent_content.encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO bm25_docs"
            "(namespace, run_id, role, content, content_hash, ts, importance, expires_at, is_leaf, parent_id) "
            "VALUES(?,?,?,?,?,?,?,?,0,NULL)",
            ("default", run_id, role, parent_content, parent_digest, time.time(), importance, expires_at),
        )
        parent_id = self.conn.execute(
            "SELECT id FROM bm25_docs WHERE namespace=? AND role=? AND content_hash=?",
            ("default", role, parent_digest),
        ).fetchone()[0]

        for child in child_contents:
            child = child.strip()
            if not child:
                continue
            child_digest = hashlib.sha256(f"{parent_id}|{child}".encode("utf-8")).hexdigest()
            self.conn.execute(
                "INSERT OR IGNORE INTO bm25_docs"
                "(namespace, run_id, role, content, content_hash, ts, importance, expires_at, is_leaf, parent_id) "
                "VALUES(?,?,?,?,?,?,?,?,1,?)",
                ("default", run_id, role, child, child_digest, time.time(), importance, expires_at, parent_id),
            )
        self.conn.commit()

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []
        rows = self.conn.execute(
            "SELECT id, content, importance, parent_id FROM bm25_docs "
            "WHERE namespace=? AND is_leaf=1 AND (expires_at IS NULL OR expires_at > ?)",
            ("default", time.time()),
        ).fetchall()
        if not rows:
            return []
        ranked = bm25_rank(query, [(r[0], r[1], r[2]) for r in rows])
        parent_by_id = {r[0]: r[3] for r in rows}

        results: list[str] = []
        seen_parents: set[int] = set()
        parent_cache: dict[int, str] = {}
        for doc_id, content, _score in ranked:
            parent_id = parent_by_id[doc_id]
            if parent_id is not None:
                if parent_id in seen_parents:
                    continue  # 同一 parent 下别的 child 已经命中过，不重复返回
                seen_parents.add(parent_id)
                if parent_id not in parent_cache:
                    prow = self.conn.execute(
                        "SELECT content FROM bm25_docs WHERE id=?", (parent_id,)
                    ).fetchone()
                    parent_cache[parent_id] = prow[0] if prow else content
                results.append(parent_cache[parent_id])
            else:
                results.append(content)
            if len(results) >= k:
                break
        return results

    def prune_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM bm25_docs WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount
