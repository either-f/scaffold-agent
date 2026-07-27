"""PostgreSQL + pgvector 语义记忆 adapter。"""
from __future__ import annotations

import hashlib
from typing import Any

from ..ports import MemoryPort


class PgVectorMemory(MemoryPort):
    """按 namespace 隔离的原消息语义记忆。"""

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

    def add(self, run_id: str, role: str, content: str) -> None:
        content = content.strip()
        if role not in {"user", "assistant"} or not content:
            return

        from pgvector import Vector

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
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
                   (namespace, run_id, role, content, content_hash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (namespace, role, content_hash) DO NOTHING""",
                (self.namespace, run_id, role, content, digest, Vector(embedding)),
            )

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []

        from pgvector import Vector

        embedding = self._embed(query)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT content FROM agent_memories
                   WHERE namespace = %s
                   ORDER BY embedding <=> %s
                   LIMIT %s""",
                (self.namespace, Vector(embedding), k),
            ).fetchall()
        return [row[0] for row in rows]
