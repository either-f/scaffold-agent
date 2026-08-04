"""Neo4j 生产图记忆 adapter：真实图数据库存实体-关系三元组，Cypher 查询。

与 `GraphMemory`（SQLite，见 memory_graph.py）同一套契约作 Fake 对照：
`MemoryPort.add/search` 存查原文事实，`add_edge/search_edges/get_neighbors`
存查三元组。namespace 在构造时固定（对齐 `PgVectorMemory` 的单实例单
namespace 模式），边/邻居查询不再重复传 namespace 参数。
"""
from __future__ import annotations

import hashlib
import time


from ...ports import MemoryPort


class Neo4jGraphMemory(MemoryPort):
    def __init__(self, uri: str, user: str, password: str, namespace: str) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ImportError("需要 graph 依赖：uv sync --extra graph") from exc
        if not uri or not user or not password or not namespace:
            raise ValueError("uri、user、password 和 namespace 均不能为空")
        self.namespace = namespace
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._init_schema()

    def close(self) -> None:
        self.driver.close()

    def _init_schema(self) -> None:
        with self.driver.session() as session:
            session.run(
                "CREATE CONSTRAINT fact_dedup IF NOT EXISTS "
                "FOR (f:Fact) REQUIRE f.dedup_key IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT entity_key IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE e.key IS UNIQUE"
            )
            session.run(
                "CREATE INDEX edge_dedup IF NOT EXISTS "
                "FOR ()-[r:REL]-() ON (r.dedup_key)"
            )

    # ------------------------------------------------------ MemoryPort contract
    def add(self, run_id: str, role: str, content: str) -> None:
        # ponytail: 无 TTL/importance 属性，跟 memory/graph.py（SQLite 对照实现）不对齐；
        # 没有可连的真实 Neo4j 验证 Cypher 改动，先不加，真要用时给 Fact 节点加同名属性+WHERE 过滤。
        content = content.strip()
        if role not in ("user", "assistant") or not content:
            return
        digest = hashlib.sha256(
            f"{self.namespace}|{role}|{content}".encode("utf-8")
        ).hexdigest()
        with self.driver.session() as session:
            session.run(
                "MERGE (f:Fact {dedup_key: $dedup_key}) "
                "ON CREATE SET f.namespace = $namespace, f.run_id = $run_id, "
                "f.role = $role, f.content = $content, f.ts = $ts",
                dedup_key=digest,
                namespace=self.namespace,
                run_id=run_id,
                role=role,
                content=content,
                ts=time.time(),
            )

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []
        words = sorted(query.split(), key=len, reverse=True)
        with self.driver.session() as session:
            result = session.run(
                "MATCH (f:Fact) WHERE f.namespace = $namespace AND f.content CONTAINS $word "
                "RETURN f.content AS content ORDER BY f.ts DESC LIMIT $k",
                namespace=self.namespace,
                word=words[0],
                k=k,
            )
            return [record["content"] for record in result]

    # --------------------------------------------------------- graph edge API
    def add_edge(self, subject: str, relation: str, object: str) -> None:
        subject = subject.strip()
        relation = relation.strip()
        object = object.strip()
        if not subject or not relation or not object:
            raise ValueError("subject、relation、object 均不能为空")
        digest = hashlib.sha256(
            f"{self.namespace}|{subject}|{relation}|{object}".encode("utf-8")
        ).hexdigest()
        with self.driver.session() as session:
            session.run(
                "MERGE (s:Entity {key: $subject_key}) "
                "ON CREATE SET s.namespace = $namespace, s.name = $subject "
                "MERGE (o:Entity {key: $object_key}) "
                "ON CREATE SET o.namespace = $namespace, o.name = $object "
                "MERGE (s)-[r:REL {dedup_key: $dedup_key}]->(o) "
                "ON CREATE SET r.namespace = $namespace, r.type = $relation, r.ts = $ts",
                subject_key=f"{self.namespace}|{subject}",
                object_key=f"{self.namespace}|{object}",
                namespace=self.namespace,
                subject=subject,
                object=object,
                relation=relation,
                dedup_key=digest,
                ts=time.time(),
            )

    def search_edges(
        self,
        subject: str | None = None,
        relation: str | None = None,
        object: str | None = None,
        k: int = 20,
    ) -> list[tuple[str, str, str]]:
        conditions = ["s.namespace = $namespace"]
        params: dict = {"namespace": self.namespace, "k": k}
        if subject:
            conditions.append("s.name = $subject")
            params["subject"] = subject
        if relation:
            conditions.append("r.type = $relation")
            params["relation"] = relation
        if object:
            conditions.append("o.name = $object")
            params["object"] = object
        where = " AND ".join(conditions)
        with self.driver.session() as session:
            result = session.run(
                f"MATCH (s:Entity)-[r:REL]->(o:Entity) WHERE {where} "
                "RETURN s.name AS subject, r.type AS relation, o.name AS object "
                "ORDER BY r.ts DESC LIMIT $k",
                **params,
            )
            return [
                (record["subject"], record["relation"], record["object"])
                for record in result
            ]

    def get_neighbors(
        self,
        node: str,
        direction: str = "both",
        k: int = 20,
    ) -> list[tuple[str, str, str, str]]:
        node = node.strip()
        if not node:
            raise ValueError("node 不能为空")
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError("direction 必须为 outgoing / incoming / both")
        key = f"{self.namespace}|{node}"
        results: list[tuple[str, str, str, str]] = []
        with self.driver.session() as session:
            if direction in ("outgoing", "both"):
                result = session.run(
                    "MATCH (s:Entity {key: $key})-[r:REL]->(o:Entity) "
                    "RETURN s.name AS subject, r.type AS relation, o.name AS object "
                    "ORDER BY r.ts DESC LIMIT $k",
                    key=key,
                    k=k,
                )
                results.extend(
                    (rec["subject"], rec["relation"], rec["object"], "outgoing")
                    for rec in result
                )
            if direction in ("incoming", "both"):
                result = session.run(
                    "MATCH (s:Entity)-[r:REL]->(o:Entity {key: $key}) "
                    "RETURN s.name AS subject, r.type AS relation, o.name AS object "
                    "ORDER BY r.ts DESC LIMIT $k",
                    key=key,
                    k=k,
                )
                results.extend(
                    (rec["subject"], rec["relation"], rec["object"], "incoming")
                    for rec in result
                )
        return results[:k]
