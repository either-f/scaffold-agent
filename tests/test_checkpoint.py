"""Checkpoint 加固测试（SPEC-86）：run_id 校验、并发写检测、schema 版本化兼容。

覆盖三条验收标准：
1. 恶意/畸形 run_id 在 RunState 构造时即被拒，到不了 Path 构造。
2. 加载无 schema_version/revision 的老 checkpoint 正常工作。
3. stale resume（内存 revision 落后于磁盘）的第二次 save 抛 CheckpointConflictError。

运行：PYTHONPATH=src python3 tests/test_checkpoint.py   （也兼容 pytest）
"""
import json
import sys
import tempfile

sys.path.insert(0, "src")

import pytest

from agent_kernel.checkpoint import CheckpointConflictError, JsonCheckpointStore
from agent_kernel.fork import fork
from agent_kernel.types import RunState


# ---------------------------------------------------------------- run_id 校验


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "..%2f..%2fetc",
        "foo/../bar",
        "foo\\bar",
        "abs\\..\\..\\escape",
        ".",
        "..",
        "has\x00null",
        "",
    ],
)
def test_malformed_run_id_rejected_at_construction(bad_id):
    """路径逃逸/注入字符的 run_id 在 RunState 构造时即 ValueError，到不了 Path。"""
    with pytest.raises(ValueError):
        RunState(run_id=bad_id)


@pytest.mark.parametrize(
    "good_id",
    [
        "scn-1",
        "turn-scope",
        "hitl-1",
        "fork-approve",
        "fork-reject",
        "abc123DEF",
        "run.with.dots",
        "run_with_underscores",
        "a" * 128,
        "0123456789abcdef0123456789abcdef",
    ],
)
def test_existing_readable_run_ids_still_valid(good_id):
    """现有测试/eval 用的可读 id 全部继续有效——校验是 deny-list 不是过严 allow-list。"""
    state = RunState(run_id=good_id)
    assert state.run_id == good_id


def test_run_id_too_long_rejected():
    with pytest.raises(ValueError):
        RunState(run_id="a" * 129)


def test_default_run_id_still_valid():
    """不传 run_id 时默认 uuid hex 仍正常生成。"""
    state = RunState()
    assert len(state.run_id) == 12


# -------------------------------------------------------- schema 版本化兼容


def test_load_old_checkpoint_without_schema_version():
    """无 schema_version/revision 的老 checkpoint（v1）经 from_dict 正常加载。"""
    old_payload = {
        "run_id": "legacy-1",
        "messages": [{"role": "user", "content": "hi", "name": None}],
        "step": 1,
        "status": "done",
        "answer": "ok",
        "pending_tool": None,
        "pending_effect_id": None,
        "forked_from": None,
        "turn": 0,  # v1 早期 checkpoint turn 可能为 0
        "context_summary": "",
        "summarized_message_count": 0,
    }
    state = RunState.from_dict(old_payload)
    assert state.run_id == "legacy-1"
    assert state.turn == 1  # v1 兼容：turn==0 被抬到 1
    assert state.revision == 0  # 老文件无 revision，默认 0
    assert state.status == "done"


def test_load_v2_checkpoint_preserves_revision_and_turn():
    """v2 checkpoint 的 revision 和 turn 原样保留，不做 turn 抬升。"""
    v2_payload = {
        "run_id": "new-1",
        "messages": [],
        "step": 2,
        "status": "paused",
        "answer": None,
        "pending_tool": {"name": "calc", "args": {}, "thought": ""},
        "pending_effect_id": None,
        "forked_from": None,
        "turn": 3,
        "context_summary": "",
        "summarized_message_count": 0,
        "revision": 7,
        "schema_version": 2,
    }
    state = RunState.from_dict(v2_payload)
    assert state.turn == 3  # v2 不做抬升
    assert state.revision == 7


def test_to_dict_emits_schema_version():
    state = RunState(run_id="emit-1")
    d = state.to_dict()
    assert d["schema_version"] == RunState.SCHEMA_VERSION
    assert d["revision"] == 0


def test_roundtrip_preserves_schema_version_and_revision(tmp_path):
    store = JsonCheckpointStore(str(tmp_path))
    state = RunState(run_id="round-1")
    store.save(state)  # save 递增 revision 到 1
    loaded = store.load("round-1")
    assert loaded is not None
    assert loaded.revision == 1
    raw = json.loads((tmp_path / "round-1" / "latest.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == RunState.SCHEMA_VERSION


def test_from_dict_ignores_unknown_future_fields():
    """向前兼容：老 from_dict 读含未知新字段的 checkpoint 时不报错（忽略未知字段）。"""
    payload = {
        "run_id": "future-1",
        "messages": [],
        "step": 0,
        "status": "running",
        "answer": None,
        "pending_tool": None,
        "pending_effect_id": None,
        "forked_from": None,
        "turn": 1,
        "context_summary": "",
        "summarized_message_count": 0,
        "revision": 0,
        "schema_version": 2,
        "some_future_field": "should be ignored",
    }
    state = RunState.from_dict(payload)
    assert state.run_id == "future-1"


# -------------------------------------------------------- 并发写检测


def test_stale_resume_raises_conflict():
    """两次 save，第二次用第一次 save 前（未递增）的 state.revision 模拟 stale resume：
    第二次 save 抛 CheckpointConflictError 而非静默覆盖更新的磁盘 checkpoint。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        state = RunState(run_id="conflict-1", turn=1, step=0)
        state.add("user", "first")
        # 记下 stale 副本（revision 仍为 0，模拟崩溃前快照）
        stale = RunState.from_dict(state.to_dict())
        stale.revision = state.revision  # 仍是 0
        stale.messages = list(state.messages)

        store.save(state)  # 磁盘 revision 变 1，内存 state.revision 也变 1

        # stale 副本 revision 仍为 0，磁盘已是 1 → 冲突
        stale.add("user", "stale resume attempt")
        with pytest.raises(CheckpointConflictError) as exc_info:
            store.save(stale)
        assert exc_info.value.run_id == "conflict-1"
        assert exc_info.value.on_disk_revision == 1
        assert exc_info.value.expected_revision == 0


def test_sequential_saves_with_correct_revision_succeed():
    """正常顺序 save（每次用上一次 save 后的 state，revision 跟上）不报冲突。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        state = RunState(run_id="seq-1", turn=1, step=0)
        store.save(state)  # revision 0→1
        assert state.revision == 1
        state.step = 1
        store.save(state)  # revision 1→2
        assert state.revision == 2
        loaded = store.load("seq-1")
        assert loaded is not None and loaded.revision == 2


def test_first_save_no_conflict():
    """全新 run 第一次 save（无 latest.json）不报冲突。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        state = RunState(run_id="fresh-1", turn=1, step=0)
        store.save(state)  # 不应抛任何异常
        assert state.revision == 1


def test_conflict_message_informative():
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonCheckpointStore(tmp)
        state = RunState(run_id="msg-1", turn=1, step=0)
        store.save(state)
        stale = RunState.from_dict(state.to_dict())
        stale.revision = 0  # 强制 stale
        stale.step = 99
        with pytest.raises(CheckpointConflictError) as exc_info:
            store.save(stale)
        msg = str(exc_info.value)
        assert "msg-1" in msg
        assert "revision=1" in msg
        assert "预期" in msg or "expected" in msg.lower()


# -------------------------------------------------------- fork revision 重置


def test_fork_resets_revision_to_zero(tmp_path):
    store = JsonCheckpointStore(str(tmp_path))
    src = RunState(run_id="fork-src-rev", turn=1, step=1, status="paused")
    store.save(src)  # src.revision 0→1
    assert src.revision == 1

    forked = fork(store, "fork-src-rev", "turn_001_step_001", new_run_id="fork-child-rev")
    assert forked.run_id == "fork-child-rev"
    assert forked.revision == 0  # fork 是新谱系，revision 重置
    assert forked.forked_from == "fork-src-rev@turn_001_step_001"


def test_fork_child_can_save_without_conflict(tmp_path):
    """fork 出来的 child（revision=0）在空目录第一次 save 不应因父 run 的 revision 冲突。"""
    store = JsonCheckpointStore(str(tmp_path))
    src = RunState(run_id="fork-save-src", turn=1, step=1, status="paused")
    store.save(src)
    forked = fork(store, "fork-save-src", "turn_001_step_001", new_run_id="fork-save-child")
    store.save(forked)  # child 目录是空的，不应冲突
    assert forked.revision == 1


if __name__ == "__main__":
    test_malformed_run_id_rejected_at_construction("../../etc/passwd")
    test_existing_readable_run_ids_still_valid("scn-1")
    test_run_id_too_long_rejected()
    test_default_run_id_still_valid()
    test_load_old_checkpoint_without_schema_version()
    test_load_v2_checkpoint_preserves_revision_and_turn()
    test_to_dict_emits_schema_version()
    with tempfile.TemporaryDirectory() as t:
        test_roundtrip_preserves_schema_version_and_revision(__import__("pathlib").Path(t))
    test_from_dict_ignores_unknown_future_fields()
    test_stale_resume_raises_conflict()
    test_sequential_saves_with_correct_revision_succeed()
    test_first_save_no_conflict()
    test_conflict_message_informative()
    with tempfile.TemporaryDirectory() as t:
        test_fork_resets_revision_to_zero(__import__("pathlib").Path(t))
    with tempfile.TemporaryDirectory() as t:
        test_fork_child_can_save_without_conflict(__import__("pathlib").Path(t))
    print("OK: checkpoint 加固测试全部通过")
