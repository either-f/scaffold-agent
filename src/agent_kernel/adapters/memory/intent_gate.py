"""IntentGatedMemory：检索前置分流层，对齐 FoxChat"两层意图分类器"。

第一层——正则规则（casual_patterns 命中直接跳过检索；scope_patterns 命中路由到
对应 scope）。第二层——语义兜底：只有正则完全没命中任何 pattern（既不是 casual
也没命中任何 scope）时，才用 classifier_model 问一句"要不要检索"，省 token
（scope 命中已经能确定"要检索"，不用再问模型）。不传 classifier_model 时退化成
纯正则版（未命中＝照常检索，不误伤）。

scope 路由：inner 可以是单个 MemoryPort（v1 用法，原样保留），也可以是
dict[scope, MemoryPort] 按 scope 路由到不同实例，找不到匹配的 scope 落到
"default" 键。

ponytail: 这不是 FoxChat 那种"同一个库按 metadata 过滤"式 scope——那需要给
MemoryPort.search() 加 scope/filter 参数，会牵动全部现有 adapter，代价太大。
这里是"每个 scope 一个独立 MemoryPort 实例"：调用方如果想让多个 scope 共用同一
底层存储，把同一个实例塞进 dict 的多个 key 即可。add() 只写 "default" 这个
key——跟 HybridGraphRAG"运行时只写 vector+keyword，图谱靠离线 ingestion"是同一个
"写路径收窄，读路径分流"思路，因为 MemoryPort.add() 不带 scope 信息，运行时
没法知道一条消息该写进哪个 scope 专用存储。
"""
from __future__ import annotations

import re

from ...ports import MemoryPort, ModelPort
from ...types import Message

DEFAULT_CASUAL_PATTERNS = [
    r"^(你好|hi+|hello|哈哈+|嗯+|哦+|好的|谢谢|嗯嗯|ok|okay)[!！。.~～\s]*$",
]

DEFAULT_SCOPE = "default"

CLASSIFY_PROMPT = (
    "判断下面这句用户输入是否需要检索历史记忆才能回答（例如涉及身份、偏好、边界"
    "约束、追问之前的内容），还是纯闲聊/寒暄可以直接回答。只输出一个词：\n"
    "retrieve 或 skip。\n\n用户输入：{query}"
)


class IntentGatedMemory(MemoryPort):
    def __init__(
        self,
        inner: MemoryPort | dict[str, MemoryPort],
        casual_patterns: list[str] | None = None,
        scope_patterns: dict[str, list[str]] | None = None,
        scope_k: dict[str, int] | None = None,
        classifier_model: ModelPort | None = None,
    ) -> None:
        if isinstance(inner, dict) and DEFAULT_SCOPE not in inner:
            raise ValueError(f'dict 形式的 inner 必须包含 "{DEFAULT_SCOPE}" 键')
        self.inner = inner
        self._casual_re = [re.compile(p, re.IGNORECASE) for p in (casual_patterns or DEFAULT_CASUAL_PATTERNS)]
        self._scope_re = {
            scope: [re.compile(p) for p in patterns] for scope, patterns in (scope_patterns or {}).items()
        }
        self.scope_k = scope_k or {}
        self.classifier_model = classifier_model

    def add(self, run_id: str, role: str, content: str) -> None:
        target = self.inner[DEFAULT_SCOPE] if isinstance(self.inner, dict) else self.inner
        target.add(run_id, role, content)

    def search(self, query: str, k: int = 5) -> list[str]:
        stripped = query.strip()
        if self._is_casual(stripped):
            return []
        scope = self._classify_scope_by_regex(stripped) or DEFAULT_SCOPE
        mem = self._memory_for(scope)
        return mem.search(query, k=self.scope_k.get(scope, k))

    def _is_casual(self, query: str) -> bool:
        if any(p.match(query) for p in self._casual_re):
            return True
        if self.classifier_model is None:
            return False
        if self._classify_scope_by_regex(query) is not None:
            return False  # scope 正则已命中，明确要检索，不用再问模型
        output = self.classifier_model.complete([Message("user", CLASSIFY_PROMPT.format(query=query))], [])
        return output.text.strip().lower().startswith("skip")

    def _classify_scope_by_regex(self, query: str) -> str | None:
        for scope, patterns in self._scope_re.items():
            if any(p.search(query) for p in patterns):
                return scope
        return None

    def _memory_for(self, scope: str) -> MemoryPort:
        if isinstance(self.inner, dict):
            return self.inner.get(scope, self.inner[DEFAULT_SCOPE])
        return self.inner
