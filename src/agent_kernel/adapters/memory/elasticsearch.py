"""Elasticsearch BM25 关键词记忆 adapter。跟 pgvector.py 同一套结构（懒加载依赖，
ImportError 给安装提示），区别在于：namespace 映射成 index（一个 namespace 一个
index，比 pgvector 的"单表+namespace 过滤"更符合 ES 惯用法），BM25 是 ES 默认
similarity，零配置；去重靠 `_id=content_hash` 做幂等 upsert，比 SQL 唯一约束更省事。

跟 bm25.py（`Bm25Memory`，纯 stdlib 实现同一套 BM25Okapi 算法）的关系：接口形态一致
（MemoryPort.add/search），`Bm25Memory` 是这个 adapter 的离线 Fake 对照。

坦诚声明：这次会话 docker daemon 没起（`docker ps` 连不上），没有真实 Elasticsearch
实例可连，这个 adapter **没能验证**——跟 pgvector/neo4j 当初一样，`uv sync --extra
keyword` 后装得上真实 elasticsearch 8.x 客户端（已确认 API 签名对得上：
`client.index(index=, id=, document=)` 幂等写入、`client.search(index=, query=,
size=)` 检索），但要连真实服务、跑 `docker compose up -d elasticsearch` 后手动验证。
"""
from __future__ import annotations

import hashlib

from ...ports import MemoryPort
from ...types import MemoryHit


class ElasticsearchMemory(MemoryPort):
    """一个 namespace 对应一个 ES index，BM25 是默认 similarity。"""

    def __init__(self, url: str, namespace: str) -> None:
        try:
            from elasticsearch import Elasticsearch  # noqa: F401
        except ImportError as exc:
            raise ImportError("需要 keyword 依赖：uv sync --extra keyword") from exc
        if not url or not namespace:
            raise ValueError("url 和 namespace 不能为空")
        self.namespace = namespace
        self.client = Elasticsearch(url)
        self._init_schema()

    def _init_schema(self) -> None:
        if not self.client.indices.exists(index=self.namespace):
            self.client.indices.create(
                index=self.namespace,
                mappings={
                    "properties": {
                        "run_id": {"type": "keyword"},
                        "role": {"type": "keyword"},
                        "content": {"type": "text"},
                        "identity": {"type": "keyword"},
                    }
                },
            )

    def add(self, run_id: str, role: str, content: str, identity: str | None = None) -> None:
        content = content.strip()
        if role not in {"user", "assistant"} or not content:
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.client.index(
            index=self.namespace,
            id=digest,
            document={"run_id": run_id, "role": role, "content": content, "identity": identity},
        )

    def search(self, query: str, k: int = 5, identity: str | None = None) -> list[MemoryHit]:
        query = query.strip()
        if not query or k <= 0:
            return []
        search_query = {"match": {"content": query}}
        if identity is not None:
            search_query = {
                "bool": {
                    "must": [search_query],
                    "should": [
                        {"term": {"identity": identity}},
                        {"bool": {"must_not": {"exists": {"field": "identity"}}}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        response = self.client.search(
            index=self.namespace,
            query=search_query,
            size=k,
        )
        return [
            MemoryHit(
                hit["_source"]["content"],
                score=float(hit["_score"]) if hit.get("_score") is not None else None,
                source="keyword",
                run_id=hit["_source"].get("run_id"),
            )
            for hit in response["hits"]["hits"]
        ]
