"""ReAct prompt 的有界上下文构建。"""
from __future__ import annotations

from ..ports import ModelPort
from ..types import Message, RunState


class ContextBuilder:
    def __init__(
        self,
        max_prompt_chars: int = 24000,
        keep_recent_messages: int = 8,
        max_summary_chars: int = 2000,
    ) -> None:
        if min(max_prompt_chars, keep_recent_messages, max_summary_chars) < 1:
            raise ValueError("上下文限制必须为正数")
        self.max_prompt_chars = max_prompt_chars
        self.keep_recent_messages = keep_recent_messages
        self.max_summary_chars = max_summary_chars

    def build(self, system: Message, state: RunState, model: ModelPort) -> list[Message]:
        state.summarized_message_count = min(
            state.summarized_message_count, len(state.messages)
        )
        prompt = self._prompt(system, state)
        if self._chars(prompt) <= self.max_prompt_chars:
            return prompt

        start = state.summarized_message_count
        cutoff = max(start, len(state.messages) - self.keep_recent_messages)
        while cutoff < len(state.messages) - 1 and self._estimated_chars(system, state, cutoff) > self.max_prompt_chars:
            cutoff += 1
        if cutoff == start:  # 单条或最近消息本身超限，不在 M3 的保证范围
            return prompt

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
        state.context_summary = summary[: self.max_summary_chars]
        state.summarized_message_count = cutoff
        return self._prompt(system, state)

    def _prompt(self, system: Message, state: RunState) -> list[Message]:
        content = system.content
        if state.context_summary:
            content += f"\n历史摘要：\n{state.context_summary}"
        return [Message("system", content), *state.messages[state.summarized_message_count :]]

    def _estimated_chars(self, system: Message, state: RunState, cutoff: int) -> int:
        return (
            len(system.content)
            + len("\n历史摘要：\n")
            + self.max_summary_chars
            + sum(len(message.content) for message in state.messages[cutoff:])
        )

    @staticmethod
    def _chars(messages: list[Message]) -> int:
        return sum(len(message.content) for message in messages)
