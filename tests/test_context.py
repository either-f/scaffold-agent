"""ContextBuilder 测试：token 感知预算、CJK/英文可比性、单条超限显式抛错。

运行：PYTHONPATH=src python3 tests/test_context.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

import pytest

from agent_kernel.planners.context import (
    ContextBudgetExceededError,
    ContextBuilder,
    estimate_tokens,
)
from agent_kernel.ports import ModelPort
from agent_kernel.types import Message, ModelOutput, RunState, ToolSpec


class StubModel(ModelPort):
    """固定返回摘要文本，供 build() 触发压缩分支。"""

    def __init__(self, summary: str = "摘要：保留目标与偏好。") -> None:
        self.summary = summary
        self.calls = 0

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelOutput:
        self.calls += 1
        return ModelOutput(self.summary)


# ----------------------------------------------------------------- estimate_tokens


def test_estimate_tokens_cjk_is_one_per_char():
    # 每个 CJK 表意文字算 1 token。
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("测试") == 2


def test_estimate_tokens_ascii_is_one_per_four_chars():
    # 拉丁文按 ~4 字符/token。
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_estimate_tokens_mixed():
    # 中英混合：CJK 逐字 + 拉丁按 4 字符。
    text = "你好world"  # 2 CJK + 5 latin => 2 + 1
    assert estimate_tokens(text) == 3


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


# ---------------------------------------------------------- CJK vs English 可比性


def test_cjk_and_english_comparable_token_budget():
    """SPEC-89 验收：信息量可比的中英文 prompt 应产生可比的 token 预算行为，
    而裸 len() 会让中文 prompt 的「字符预算」远小于英文。"""
    # 同样表达「你好世界」语义：中文 4 字符，英文 "hello world" 11 字符。
    chinese = "你好世界"
    english = "hello world"
    # 裸字符数差异巨大：4 vs 11。
    assert len(chinese) * 2 < len(english)
    # token 估算后更接近：中文 4 token，英文 2 token（11//4=2）——不再 2.7x 失真。
    ct, et = estimate_tokens(chinese), estimate_tokens(english)
    # 两者都在同一量级（都不超过 5），不再像字符数那样差近 3 倍。
    assert abs(ct - et) <= 5

    # 用一段更长的等价文本验证：预算门槛对两种语言都按 token 触发。
    long_cn = "请记住这一轮的对话内容。" * 50   # 600 CJK 字符
    long_en = "Please remember this round of dialogue. " * 50  # 2150 latin 字符
    cn_tokens = estimate_tokens(long_cn)
    en_tokens = estimate_tokens(long_en)
    # 字符数：600 vs 2150（3.6x）；token 数差距显著收窄。
    char_ratio = len(long_en) / len(long_cn)
    token_ratio = max(cn_tokens, en_tokens) / min(cn_tokens, en_tokens)
    assert token_ratio < char_ratio  # token 估算后差距明显小于字符数差距


# --------------------------------------------------------- 单条超限：显式抛错


def test_single_oversized_message_raises_not_silent():
    """SPEC-89 验收：单条消息本身超预算时，build() 不再静默返回超限 prompt，
    而是抛 ContextBudgetExceededError。"""
    builder = ContextBuilder(max_prompt_tokens=500, keep_recent_messages=2, max_summary_tokens=100)
    system = Message("system", "你是助手。")
    state = RunState()
    # 单条远超预算的 user 消息。
    state.add("user", "巨长内容" * 500)
    model = StubModel()
    with pytest.raises(ContextBudgetExceededError) as exc_info:
        builder.build(system, state, model)
    err = exc_info.value
    assert err.tokens > 500
    assert err.budget == 500
    assert err.role == "user"
    # RunState 审计记录未被篡改。
    assert len(state.messages) == 1
    assert state.messages[0].content == "巨长内容" * 500


def test_system_prompt_oversize_raises():
    """system prompt 本身（含摘要预留）超限时也显式抛错。"""
    builder = ContextBuilder(max_prompt_tokens=20, keep_recent_messages=2, max_summary_tokens=10)
    system = Message("system", "x" * 100)  # 100 latin 字符 ≈ 25 token > 20
    state = RunState()
    state.add("user", "hi")
    with pytest.raises(ContextBudgetExceededError):
        builder.build(system, state, StubModel())


def test_normal_compression_still_works():
    """多轮历史触发摘要压缩、且能压进预算时，build() 正常返回，不抛错。"""
    builder = ContextBuilder(max_prompt_tokens=300, keep_recent_messages=2, max_summary_tokens=80)
    system = Message("system", "你是助手。")
    state = RunState()
    # 塞入足够多轮、每轮都不算太大，让 cutoff 能把 prompt 压进预算。
    for i in range(10):
        state.add("user", f"第 {i} 轮用户消息，内容稍长。" * 3)
        state.add("assistant", f"第 {i} 轮回复。" * 3)
    prompt = builder.build(system, state, StubModel())
    assert builder._tokens(prompt) <= 300
    assert state.summarized_message_count > 0
    assert state.context_summary != ""


def test_deprecated_char_alias_still_accepted():
    """旧调用方传 max_prompt_chars / max_summary_chars 仍可用（弃用别名）。"""
    b = ContextBuilder(max_prompt_chars=12345, max_summary_chars=678)
    assert b.max_prompt_tokens == 12345
    assert b.max_summary_tokens == 678
    # 旧属性名也暴露，避免外部 AttributeError。
    assert b.max_prompt_chars == 12345
    assert b.max_summary_chars == 678


def test_summary_truncated_to_token_budget_with_marker():
    """摘要超 token 预算时被截断并标注 [截断，...]。"""
    long_summary = "摘要内容很长。" * 50  # 350 CJK 字符 ≈ 350 token
    builder = ContextBuilder(max_prompt_tokens=10000, keep_recent_messages=2, max_summary_tokens=100)
    truncated = builder._truncate_to_tokens(long_summary, 100)
    assert estimate_tokens(truncated) <= 110  # 留一点余量给 marker 文本
    assert "[截断" in truncated


if __name__ == "__main__":
    test_estimate_tokens_cjk_is_one_per_char()
    test_estimate_tokens_ascii_is_one_per_four_chars()
    test_estimate_tokens_mixed()
    test_estimate_tokens_empty()
    test_cjk_and_english_comparable_token_budget()
    test_single_oversized_message_raises_not_silent()
    test_system_prompt_oversize_raises()
    test_normal_compression_still_works()
    test_deprecated_char_alias_still_accepted()
    test_summary_truncated_to_token_budget_with_marker()
    print("OK: context 测试全部通过")
