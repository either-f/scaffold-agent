"""Milvus 向量记忆 adapter。跟 pgvector.py 同一套结构（懒加载依赖、embedding 用
litellm、content_hash 查重），区别只在存储后端。

`uri` 对 pymilvus 的 `MilvusClient` 透明：本地文件路径（如 `"./hybrid_rag.db"`）走
Milvus Lite 嵌入模式，`"http://host:19530"` 走真实 Milvus server，同一份代码两种
模式都能跑，不用单独写 Fake。

坦诚声明：Milvus Lite（`milvus-lite` 包）官方只发布 Linux/macOS wheel，这台 Windows
机器上 `pip install pymilvus[milvus_lite]` 装不上 `milvus-lite` 本体（`pymilvus` 装得上，
但嵌入模式连不了）——试过了，`MilvusClient(uri="./xxx.db")` 直接报
`ConnectionConfigException: milvus-lite is required`。所以这个 adapter 这次会话
**没能验证**，跟 pgvector/neo4j 当初一样，需要真实 Milvus server（`http://host:19530`）
或 Linux/macOS 环境下的 Milvus Lite 才能跑通，见 evals/run_hybrid_rag.py 的
`--mode real`。
"""
from __future__ import annotations

import hashlib
from typing import Any

from ...ports import MemoryPort


class MilvusMemory(MemoryPort):
    """按 namespace（= collection）隔离的原消息语义记忆。"""

    def __init__(
        self,
        uri: str,
        namespace: str,
        model: str = "dashscope/text-embedding-v4",
        dimensions: int = 1024,
        **embedding_kwargs: Any,
    ) -> None:
        try:
            import litellm  # noqa: F401
            from pymilvus import MilvusClient  # noqa: F401
        except ImportError as exc:
            raise ImportError("需要 milvus/model 依赖：uv sync --extra milvus --extra model") from exc
        if not uri or not namespace or not model:
            raise ValueError("uri、namespace 和 model 不能为空")
        if dimensions <= 0:
            raise ValueError("dimensions 必须大于 0")
        self.uri = uri
        self.namespace = namespace
        self.model = model
        self.dimensions = dimensions
        self.embedding_kwargs = embedding_kwargs
        self.client = MilvusClient(uri=uri)
        self._init_schema()

    def _init_schema(self) -> None:
        if not self.client.has_collection(self.namespace):
            self.client.create_collection(
                collection_name=self.namespace,
                dimension=self.dimensions,
                auto_id=True,
                enable_dynamic_field=True,
            )

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

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        exists = self.client.query(
            collection_name=self.namespace,
            filter=f'content_hash == "{digest}"',
            limit=1,
        )
        if exists:
            return
        embedding = self._embed(content)
        self.client.insert(
            collection_name=self.namespace,
            data=[
                {
                    "vector": embedding,
                    "run_id": run_id,
                    "role": role,
                    "content": content,
                    "content_hash": digest,
                }
            ],
        )

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []

        embedding = self._embed(query)
        results = self.client.search(
            collection_name=self.namespace,
            data=[embedding],
            limit=k,
            output_fields=["content"],
        )
        hits = results[0] if results else []
        return [hit["entity"]["content"] for hit in hits]
