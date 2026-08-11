"""mem0 真实记忆 adapter：LLM 抽取+混合检索，跟 CompositeMemory 手动分层是两套记忆栈，二选一。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...ports import MemoryPort


class Mem0Memory(MemoryPort):
    """mem0（LLM=DeepSeek、embedder=DashScope OpenAI 兼容模式、向量库=本地 Chroma）。

    mem0 add() 内部自带 LLM 抽取/去重，跟 CompositeMemory + 离线巩固脚本职责重叠，
    两者是"整套记忆栈二选一"而非组合，见 ADR-0011。
    """

    def __init__(
        self,
        namespace: str,
        persist_dir: str = "runs/mem0",
        llm_model: str = "deepseek-chat",
        embedding_model: str = "text-embedding-v4",
        embedding_dims: int = 1024,
        deepseek_api_key: str | None = None,
        dashscope_api_key: str | None = None,
    ) -> None:
        # mem0 默认经 PostHog 上报匿名用量遥测；模块级常量在 import 时读取一次环境变量，
        # 故须在 `from mem0 import Memory` 之前设置。setdefault 只提供默认关闭，
        # 调用方在此之前显式设过 MEM0_TELEMETRY=True 则尊重其选择。
        os.environ.setdefault("MEM0_TELEMETRY", "False")
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise ImportError("需要 mem0 依赖：uv sync --extra mem0") from exc
        if not namespace:
            raise ValueError("namespace 不能为空")
        deepseek_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY")
        dashscope_key = dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not deepseek_key or not dashscope_key:
            raise ValueError("需要 DEEPSEEK_API_KEY（LLM 抽取）与 DASHSCOPE_API_KEY（embedding）")

        self.namespace = namespace
        # ponytail: namespace 直接当 Chroma collection_name，Chroma 对合法字符有限制
        # （3-63 位，字母数字/下划线/连字符，首尾须字母数字）；真要接入任意 namespace
        # 时在这里加一层哈希化命名。
        self._memory = Memory.from_config(
            {
                "llm": {
                    "provider": "deepseek",
                    "config": {"model": llm_model, "api_key": deepseek_key},
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": embedding_model,
                        "embedding_dims": embedding_dims,
                        "api_key": dashscope_key,
                        "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    },
                },
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": namespace,
                        "path": str(Path(persist_dir) / namespace),
                    },
                },
            }
        )

    def add(self, run_id: str, role: str, content: str) -> None:
        content = content.strip()
        if role not in {"user", "assistant"} or not content:
            return
        self._memory.add(
            [{"role": role, "content": content}],
            user_id=self.namespace,
            run_id=run_id,
        )

    def search(self, query: str, k: int = 5) -> list[str]:
        query = query.strip()
        if not query or k <= 0:
            return []
        result: Any = self._memory.search(query, filters={"user_id": self.namespace}, top_k=k)
        items = result.get("results", []) if isinstance(result, dict) else result
        return [item["memory"] for item in items if item.get("memory")]
