"""EphemeralStatePort 单测：来源优先级仲裁 + TTL 过期。

运行：PYTHONPATH=src python3 tests/test_ephemeral_state.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.state.ephemeral import InMemoryEphemeralState
from agent_kernel.ports import StateUpdate


def test_lower_rank_cannot_override_unexpired_higher_rank():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("愤怒", source="user_explicit", ttl_turns=5), turn=1)
    state.set("r1", "emotion", StateUpdate("平静", source="runtime", ttl_turns=5), turn=2)
    assert state.get("r1", "emotion", turn=2).value == "愤怒"  # runtime 压不过 user_explicit


def test_higher_rank_overrides_lower_rank():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("平静", source="runtime", ttl_turns=5), turn=1)
    state.set("r1", "emotion", StateUpdate("愤怒", source="user_explicit", ttl_turns=5), turn=2)
    assert state.get("r1", "emotion", turn=2).value == "愤怒"


def test_expired_slot_lets_lower_rank_override():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("愤怒", source="user_explicit", ttl_turns=3), turn=1)
    state.set("r1", "emotion", StateUpdate("平静", source="runtime", ttl_turns=3), turn=4)  # 3 轮后已过期
    assert state.get("r1", "emotion", turn=4).value == "平静"


def test_get_returns_none_once_ttl_elapsed_even_without_new_write():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("愤怒", source="user_explicit", ttl_turns=3), turn=1)
    assert state.get("r1", "emotion", turn=3) is not None  # 还没到 3 轮差
    assert state.get("r1", "emotion", turn=4) is None  # turn - set_turn(1) = 3 >= ttl_turns(3)，过期


def test_same_rank_higher_confidence_wins():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("低置信", source="runtime", confidence=0.3, ttl_turns=5), turn=1)
    state.set("r1", "emotion", StateUpdate("高置信", source="runtime", confidence=0.9, ttl_turns=5), turn=1)
    assert state.get("r1", "emotion", turn=1).value == "高置信"


def test_same_rank_lower_confidence_does_not_override():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("高置信", source="runtime", confidence=0.9, ttl_turns=5), turn=1)
    state.set("r1", "emotion", StateUpdate("低置信", source="runtime", confidence=0.3, ttl_turns=5), turn=1)
    assert state.get("r1", "emotion", turn=1).value == "高置信"


def test_same_rank_same_confidence_unchanged_value_is_noop():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("平静", source="runtime", ttl_turns=5), turn=1)
    state.set("r1", "emotion", StateUpdate("平静", source="runtime", ttl_turns=5), turn=3)
    # 值没变，不应刷新 set_turn；turn=1 存入 ttl=5，turn=6 时应已过期
    assert state.get("r1", "emotion", turn=6) is None


def test_different_run_ids_are_isolated():
    state = InMemoryEphemeralState()
    state.set("r1", "emotion", StateUpdate("愤怒", source="user_explicit", ttl_turns=5), turn=1)
    assert state.get("r2", "emotion", turn=1) is None


def test_unknown_key_returns_none():
    state = InMemoryEphemeralState()
    assert state.get("r1", "nonexistent", turn=1) is None


if __name__ == "__main__":
    test_lower_rank_cannot_override_unexpired_higher_rank()
    test_higher_rank_overrides_lower_rank()
    test_expired_slot_lets_lower_rank_override()
    test_get_returns_none_once_ttl_elapsed_even_without_new_write()
    test_same_rank_higher_confidence_wins()
    test_same_rank_lower_confidence_does_not_override()
    test_same_rank_same_confidence_unchanged_value_is_noop()
    test_different_run_ids_are_isolated()
    test_unknown_key_returns_none()
    print("OK: EphemeralStatePort 测试全部通过")
