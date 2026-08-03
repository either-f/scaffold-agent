"""ReAct prompt 的有界上下文构建。

预算单位是「估算 token 数」，不是字符数：字符数在 CJK / 混合语言场景下是 token
数的劣质近似（同一信息量的中文 prompt 字符数远少于英文，但 token 数更接近）。
这里用一个零第三方依赖、CJK 感知的启发式估算（见 ``estimate_tokens``），
足以做预算门槛；需要精确 token 数时再上 tiktoken，但本模块刻意保持依赖-free。
"""
from __future__ import annotations

from ..ports import ModelPort
from ..types import Message, RunState


def estimate_tokens(text: str) -> int:
    """零依赖的 token 数估算：CJK 表意文字按 ~1 token/字，其余按 ~4 字符/token。

    选择启发式而非引入 tiktoken 的理由：
    - 本仓库核心坚持零第三方（``evals/check_core_imports.py`` 门禁），``planners/``
      虽允许第三方，但预算门槛只需相对稳定的不需要精确到个位；
    - 经验上 BPE 分词器对 CJK 基本一字一 token，对拉丁文大致 4 字符/token，本启发式
      在两种语言下偏差都在可接受范围内，且对「中英信息量可比时 token 预算也可比」
      这一目标比裸 ``len()`` 好得多；
    - 若日后某 provider 的真实 token 数与本估算系统性偏差大到影响行为，再在 adapter
      层注入精确 tokenizer，本函数作为默认兜底。
    """
    cjk = 0
    other = 0
    for ch in text:
        # 覆盖 CJK 统一表意、扩展 A、兼容表意、日文假名、韩文音节等 BPE 通常一字一 token 的范围。
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF        # CJK 统一表意
            or 0x3400 <= cp <= 0x4DBF     # CJK 扩展 A
            or 0xF900 <= cp <= 0xFAFF     # CJK 兼容表意
            or 0x3040 <= cp <= 0x30FF     # 平假名 + 片假名
            or 0xAC00 <= cp <= 0xD7AF     # 韩文音节
            or 0xFF00 <= cp <= 0xFFEF     # 全角字符
        ):
            cjk += 1
        else:
            other += 1
    # other / 4 对齐英文 4 字符/token 的经验值；至少给 0 token 的空串返回 0。
    return cjk + (other // 4)


class ContextBudgetExceededError(RuntimeError):
    """单条消息（或 system prompt 本身）已超预算，且无任何 cutoff 能把 prompt 压进预算。

    选择「抛错」而非「静默截断」（SPEC-89 要求二选一的显式处理）的理由：
    - 唯一调用方 ``ReactPlanner.step`` → ``AgentKernel._drive`` 不具备「跳过某条消息」
      的语义——截断 ``RunState.messages`` 里的审计记录不可接受，只截断 outgoing prompt
      又会让模型看到被阉割的上下文而无法自救；
    - 抛错把决策权交回调用方（kernel / 上层应用），它们可以选：拆分输入、换更大窗口的
      模型、或显式 offload 长内容后重试。这比在 ContextBuilder 内部偷偷截断更诚实。
    """

    def __init__(self, message: str, *, role: str | None, index: int | None, tokens: int, budget: int) -> None:
        self.message = message
        self.role = role
        self.index = index
        self.tokens = tokens
        self.budget = budget
        loc = f"（role={role}, index={index}）" if role is not None else ""
        super().__init__(
            f"上下文预算超限{loc}：该消息/提示估算约 {tokens} token，超出预算 {budget} token，"
            f"且任何 cutoff 都无法压进预算。请拆分输入、换更大窗口的模型，或先 offload 长内容。"
        )


class ContextBuilder:
    def __init__(
        self,
        max_prompt_tokens: int | None = None,
        keep_recent_messages: int = 8,
        max_summary_tokens: int | None = None,
        *,
        # 弃用别名：旧调用方仍传 max_prompt_chars / max_summary_chars，按「字符数 ≈ CJK
        # 一字一 token」的旧基线等价换算后接受。SPEC-89 要求重命名时保留可用别名。
        max_prompt_chars: int | None = None,
        max_summary_chars: int | None = None,
    ) -> None:
        # 旧参数兼容：显式传旧名时按原值用（行为等价，因为 CJK 一字一 token 时 1 字符≈1 token）。
        if max_prompt_tokens is None:
            max_prompt_tokens = max_prompt_chars if max_prompt_chars is not None else 24000
        if max_summary_tokens is None:
            max_summary_tokens = max_summary_chars if max_summary_chars is not None else 2000
        if min(max_prompt_tokens, keep_recent_messages, max_summary_tokens) < 1:
            raise ValueError("上下文限制必须为正数")
        self.max_prompt_tokens = max_prompt_tokens
        self.keep_recent_messages = keep_recent_messages
        self.max_summary_tokens = max_summary_tokens
        # 暴露旧名给可能读属性的旧代码，避免 AttributeError。
        self.max_prompt_chars = max_prompt_tokens
        self.max_summary_chars = max_summary_tokens

    def build(self, system: Message, state: RunState, model: ModelPort) -> list[Message]:
        state.summarized_message_count = min(
            state.summarized_message_count, len(state.messages)
        )
        prompt = self._prompt(system, state)
        if self._tokens(prompt) <= self.max_prompt_tokens:
            return prompt

        start = state.summarized_message_count
        cutoff = max(start, len(state.messages) - self.keep_recent_messages)
        while cutoff < len(state.messages) - 1 and self._estimated_tokens(system, state, cutoff) > self.max_prompt_tokens:
            cutoff += 1
        if cutoff == start:
            # 单条或最近消息本身超限，不在 M3 的保证范围。
            # 旧实现静默 return prompt（超限 prompt 原样返回），调用方无信号；
            # 现在显式抛错，让 kernel/上层决定如何处理（见 ContextBudgetExceededError 文档）。
            self._raise_oversized(system, state, start)

        new_history = "\n".join(
            f"[{message.role}{':' + message.name if message.name else ''}] {message.content}"
            for message in state.messages[start:cutoff]
        )
        summary_request = (
            f"已有摘要：\n{state.context_summary or '(无)'}\n\n"
            f"新增历史：\n{new_history}"
        )
        output = model.complete(
            [
                Message(
                    "system",
                    "你负责增量压缩对话历史。只输出摘要文本，保留目标、偏好、关键事实、"
                    "决策和未解决事项；删除重复内容与原始长工具输出。",
                ),
                Message("user", summary_request),
            ],
            [],
        )
        summary = output.text.strip()
        if not summary:
            raise ValueError("上下文摘要模型返回空响应")
        # 摘要按 token 预算截断：用 estimate_tokens 量长度，超限时按字符比例切。
        state.context_summary = self._truncate_to_tokens(summary, self.max_summary_tokens)
        state.summarized_message_count = cutoff
        return self._prompt(system, state)

    def _raise_oversized(self, system: Message, state: RunState, start: int) -> None:
        """定位最可能超限的那条消息并抛 ContextBudgetExceededError。"""
        budget = self.max_prompt_tokens
        sys_tokens = estimate_tokens(system.content) + estimate_tokens("\n历史摘要：\n") + self.max_summary_tokens
        if sys_tokens > budget:
            raise ContextBudgetExceededError(
                "system prompt（含摘要预留）本身超限",
                role=system.role,
                index=None,
                tokens=sys_tokens,
                budget=budget,
            )
        # 找出 [start:] 里单条估算 token 最高的那条（通常是罪魁）。
        worst_idx = start
        worst_tokens = 0
        for i in range(start, len(state.messages)):
            t = estimate_tokens(state.messages[i].content)
            if t > worst_tokens:
                worst_tokens = t
                worst_idx = i
        msg = state.messages[worst_idx]
        raise ContextBudgetExceededError(
            "单条消息超限，无法通过 cutoff 压进预算",
            role=msg.role,
            index=worst_idx,
            tokens=worst_tokens,
            budget=budget,
        )

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """按 token 预算截断摘要：超出时按字符比例切并标注。"""
        if estimate_tokens(text) <= max_tokens:
            return text
        # 二分查找最大字符前缀使其 token 数 <= max_tokens。
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_tokens(text[:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        original_tokens = estimate_tokens(text)
        return f"{text[:lo]}[截断，原始约 {original_tokens} token]"

    def _prompt(self, system: Message, state: RunState) -> list[Message]:
        content = system.content
        if state.context_summary:
            content += f"\n历史摘要：\n{state.context_summary}"
        return [Message("system", content), *state.messages[state.summarized_message_count :]]

    def _estimated_tokens(self, system: Message, state: RunState, cutoff: int) -> int:
        return (
            estimate_tokens(system.content)
            + estimate_tokens("\n历史摘要：\n")
            + self.max_summary_tokens
            + sum(estimate_tokens(message.content) for message in state.messages[cutoff:])
        )

    @staticmethod
    def _tokens(messages: list[Message]) -> int:
        return sum(estimate_tokens(message.content) for message in messages)
