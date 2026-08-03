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
    # SPEC-80 Req1：不能把源 run 的 pending_effect_id 带进分支——那会让两个分支
    # 共享同一行 effect（key=旧 run_id:turn:step），互相串结果。fork() 保持纯函数、
    # 不接 EffectLedger，所以这里选择"清空"：分支上第一次 resume() 会在
    # kernel._run_pending_tool 里用分支自己的 run_id 重新 propose 一行。
    forked.pending_effect_id = None
    # fork 是新谱系，revision 从 0 重新计数，与源 run 的计数器独立。
    forked.revision = 0
    return forked
