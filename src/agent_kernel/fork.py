"""从历史 checkpoint 分支出独立 run（ADR-0009）。纯读，不写盘；
一个函数，不是类——只有一个操作，不需要包一层接口。

kernel.py 零改动：fork 出来的 RunState 直接交给现有、未改动的
AgentKernel(...).resume() 就能继续跑，跟正常恢复没有区别。

SPEC-94: fork() 新增可选 bus 参数，在返回前发一条 run.forked 事件，
携带 run_id / forked_from，使 event_sourcing.reduce 能从事件流重建
forked_from（此前该字段只活在 RunState 里，事件流重建不出来）。
bus 缺省为 None，保持与既有调用方（evals/run_fork.py）100% 兼容；
SPEC-80 若也给 fork() 加参数，二者都在函数末尾附近、互不冲突。
"""
from __future__ import annotations

import re
import uuid

from .checkpoint import JsonCheckpointStore
from .events import EventBus
from .types import Event, RunState

_LABEL_RE = re.compile(r"turn_(\d+)_step_(\d+)")


def fork(
    store: JsonCheckpointStore,
    source_run_id: str,
    checkpoint: str,
    new_run_id: str | None = None,
    bus: EventBus | None = None,
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
    # SPEC-94: 事件流里也要能看出这是 fork 出来的 run。bus 缺省 None，既有调用方无感。
    # 携带 turn/step 使 event_sourcing.reduce 能从事件流完整重建 fork 点的状态。
    if bus is not None:
        bus.publish(
            Event(
                "run.forked",
                {
                    "run_id": forked.run_id,
                    "forked_from": forked.forked_from,
                    "source_run_id": source_run_id,
                    "checkpoint": checkpoint,
                    "turn": forked.turn,
                    "step": forked.step,
                },
            )
        )
    return forked
