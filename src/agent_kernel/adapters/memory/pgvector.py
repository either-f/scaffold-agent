"""PostgreSQL + pgvector 语义记忆 adapter。"""
from __future__ import annotations

import hashlib
from typing import Any

from ...ports import MemoryPort
from ...types import MemoryHit


class PgVectorMemory(MemoryPort):
    """按 namespace 隔离的原消息语义记忆。

    SPEC-90：search 返回带真实余弦相似度分数的 MemoryHit（source="semantic"）；
    identity 参数作为构造期 namespace 之上的附加过滤，使单个适配器实例可安全服务
    多个 identity（identity 列可空，默认 None 兼容既有未分级行）。
    """

    def __init__(
        self,
        dsn: str,
        namespace: str,
        model: str = "dashscope/text-embedding-v4",
        dimensions: int = 1024,
        **embedding_kwargs: Any,
    ) -> None:
        try:
            import litellm  # noqa: F401
            import pgvector  # noqa: F401
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise ImportError("需要 memory/model 依赖：uv sync --extra memory --extra model") from exc
        if not dsn or not namespace or not model:
            raise ValueError("dsn、namespace 和 model 不能为空")
        if dimensions <= 0:
            raise ValueError("dimensions 必须大于 0")
        self.dsn = dsn
        self.namespace = namespace
        self.model = model
        self.dimensions = dimensions
        self.embedding_kwargs = embedding_kwargs
        self._init_schema()

    def _init_schema(self) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        with psycopg.connect(self.dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS agent_memories (
                    id BIGSERIAL PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    content_hash CHAR(64) NOT NULL,
                    embedding vector({self.dimensions}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (namespace, role, content_hash)
                )"""
            )
            # SPEC-90: 增量加 identity 列（可空）+ 索引
            cols = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'agent_memories'"
                ).fetchall()
            }
            if "identity" not in cols:
                conn.execute("ALTER TABLE agent_memories ADD COLUMN identity TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_memories_identity "
                "ON agent_memories(identity)"
            )

    def _connect(self):
        import psycopg
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(self.dsn)
        register_vector(conn)
        return conn

    def _embed(self, text: str) -> list[float]:
        import litellm

        response = litellm.embedding(
            model=self.model,
            input=[text],
            dimensions=self.dimensions,
            **self.embedding_kwargs,
        )
        data = response.get("data") if isinstance(response, dict) else response.data
        if not data:
            raise ValueError("embedding 返回空数据")
        item = data[0]
        vector = item.get("embedding") if isinstance(item, dict) else item.embedding
        if not isinstance(vector, list) or len(vector) != self.dimensions:
            raise ValueError(f"embedding 维度错误：期望 {self.dimensions}")
        return [float(value) for value in vector]

    def add(self, run_id: str, role: str, content: str, identity: str | None = None) -> None:
        # ponytail: 无 TTL/importance 列，跟 memory/sqlite.py、memory/graph.py 不对齐；
        # 没有可连的真实 Postgres 验证 schema 迁移，先不加，真要用时照抄那两个文件的列+过滤条件。
        content = content.strip()
        if role not in {"user", "assistant"} or not content:
            return

        from pgvector import Vector

        dedup_content = content if identity is None else f"{identity}\0{content}"
        digest = hashlib.sha256(dedup_content.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            exists = conn.execute(
                """SELECT 1 FROM agent_memories
                   WHERE namespace = %s AND role = %s AND content_hash = %s""",
                (self.namespace, role, digest),
            ).fetchone()
        if exists:
            return
        embedding = self._embed(content)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_memories
                   (namespace, run_id, role, content, content_hash, embedding, identity)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (namespace, role, content_hash) DO NOTHING""",
                (self.namespace, run_id, role, content, digest, Vector(embedding), identity),
            )

    def search(self, query: str, k: int = 5, identity: str | None = None) -> list[MemoryHit]:
        query = query.strip()
        if not query or k <= 0:
            return []

        from pgvector import Vector

        embedding = self._embed(query)
        # `<=>` 返回余弦距离（0=完全相同，2=完全相反）；相似度 = 1 - 距离。
        # identity=None 时不按 identity 过滤；非 None 时叠加该 identity 过滤 +
        # 兼容历史未分级行（identity IS NULL）。
        if identity is None:
            rows = self.conn_execute(
                """SELECT content, run_id, 1 - (embedding <=> %s) AS score
                   FROM agent_memories
                   WHERE namespace = %s
                   ORDER BY embedding <=> %s
                   LIMIT %s""",
                (Vector(embedding), self.namespace, Vector(embedding), k),
            )
        else:
            rows = self.conn_execute(
                """SELECT content, run_id, 1 - (embedding <=> %s) AS score
                   FROM agent_memories
                   WHERE namespace = %s AND (identity = %s OR identity IS NULL)
                   ORDER BY embedding <=> %s
                   LIMIT %s""",
                (Vector(embedding), self.namespace, identity, Vector(embedding), k),
            )
        return [
            MemoryHit(content, score=float(score), source="semantic", run_id=run_id)
            for content, run_id, score in rows
        ]

    def conn_execute(self, sql: str, params: tuple):
        """打开一次性连接执行查询并返回 rows。"""
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()
