"""BM25 关键词记忆 adapter：不是占位 Fake，是真的 BM25Okapi 算法（k1=1.5, b=0.75
标准默认值），纯 stdlib + SQLite 持久化语料，零外部依赖。

跟 ElasticsearchMemory 的关系：接口形态一致（MemoryPort.add/search），充当它的离线
Fake 对照（跟 GraphMemory 之于 Neo4jGraphMemory 一样），同时自身就是一个能打的轻量
BM25 后端，不是凑数的占位符——真做了词频/文档频率统计，不是 LIKE 化名。

ponytail: search() 时对 namespace 内全部文档现算 BM25 分数，O(namespace 文档数)
全表 scan，没有倒排索引；几千文档量级够用，海量语料换真 Elasticsearch。
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


def bm25_rank(query: str, docs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """docs: [(content, importance), ...]；返回按 BM25 分数（乘 importance 加权）
    降序排列、只保留分数 > 0（有词项命中）的 [(content, score), ...]。"""
    query_terms = tokenize(query)
    if not query_terms or not docs:
        return []
    doc_tokens = [tokenize(content) for content, _ in docs]
    doc_lens = [len(t) for t in doc_tokens]
    avgdl = sum(doc_lens) / len(doc_lens) if doc_lens else 0.0
    n = len(docs)
    df: Counter[str] = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))

    scored: list[tuple[str, float]] = []
    for (content, importance), tokens, dl in zip(docs, doc_tokens, doc_lens):
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
            scored.append((content, score * importance))
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return scored


class Bm25Memory(MemoryPort):
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS bm25_docs("
            "id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, run_id TEXT, role TEXT NOT NULL, "
            "content TEXT NOT NULL, content_hash TEXT NOT NULL, ts REAL, "
            "importance REAL NOT NULL DEFAULT 1.0, expires_at REAL, "
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

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []
        rows = self.conn.execute(
            "SELECT content, importance FROM bm25_docs "
            "WHERE namespace=? AND (expires_at IS NULL OR expires_at > ?)",
            ("default", time.time()),
        ).fetchall()
        ranked = bm25_rank(query, [(r[0], r[1]) for r in rows])
        return [content for content, _ in ranked[:k]]

    def prune_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM bm25_docs WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount
