"""从历史 checkpoint 分支出独立 run（ADR-0009）。纯读，不写盘；
一个函数，不是类——只有一个操作，不需要包一层接口。

kernel.py 零改动：fork 出来的 RunState 直接交给现有、未改动的
AgentKernel(...).resume() 就能继续跑，跟正常恢复没有区别。
"""
from __future__ import annotations

import re
import uuid

from .checkpoint import JsonCheckpointStore
from .types import RunState

_LABEL_RE = re.compile(r"turn_(\d+)_step_(\d+)")


def fork(
    store: JsonCheckpointStore,
    source_run_id: str,
    checkpoint: str,
    new_run_id: str | None = None,
) -> RunState:
    match = _LABEL_RE.fullmatch(checkpoint)
    if not match:
        raise ValueError(f"checkpoint 标签应为 turn_NNN_step_NNN，收到: {checkpoint!r}")
    source = store.load_step(source_run_id, int(match.group(1)), int(match.group(2)))
    if source is None:
        raise FileNotFoundError(f"未找到快照 {source_run_id}/{checkpoint}")

    forked = RunState.from_dict(source.to_dict())  # 序列化往返 = 不共享引用的深拷贝
    forked.run_id = new_run_id or uuid.uuid4().hex[:12]
    forked.forked_from = f"{source_run_id}@{checkpoint}"
    return forked
