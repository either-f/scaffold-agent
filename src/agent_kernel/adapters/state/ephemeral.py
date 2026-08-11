"""内存版 EphemeralStatePort：进程内 dict，重启即丢——符合"易失状态"语义，
真要跨进程/持久化时换 Redis adapter，接口不变。

仲裁顺序（对齐 FoxChat 情绪覆盖规则）：
1. 已存值过期（turn - 存入时的 turn >= 存入时的 ttl_turns）→ 新值直接覆盖，无视来源。
2. 未过期时比来源等级（_SOURCE_RANK，未知来源按 0 处理，永远输给已知来源）。
3. 来源等级相同比 confidence，高的赢。
4. 全相同时比值是否变化，变了才写（没变化直接跳过，避免刷新 TTL 造成状态假性"续命"）。
"""
from __future__ import annotations

from ...ports import EphemeralStatePort, StateUpdate

_SOURCE_RANK = {"user_explicit": 3, "runtime": 2, "summary": 1}


def _should_replace(old: StateUpdate, old_turn: int, new: StateUpdate, turn: int) -> bool:
    expired = turn - old_turn >= old.ttl_turns
    if expired:
        return True
    old_rank = _SOURCE_RANK.get(old.source, 0)
    new_rank = _SOURCE_RANK.get(new.source, 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    if new.confidence != old.confidence:
        return new.confidence > old.confidence
    return new.value != old.value


class InMemoryEphemeralState(EphemeralStatePort):
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[StateUpdate, int]] = {}

    def set(self, run_id: str, key: str, update: StateUpdate, turn: int) -> None:
        slot = (run_id, key)
        existing = self._store.get(slot)
        if existing is None or _should_replace(existing[0], existing[1], update, turn):
            self._store[slot] = (update, turn)

    def get(self, run_id: str, key: str, turn: int) -> StateUpdate | None:
        existing = self._store.get((run_id, key))
        if existing is None:
            return None
        update, set_turn = existing
        if turn - set_turn >= update.ttl_turns:
            return None
        return update
