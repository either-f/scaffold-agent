# ADR-0009: 事件溯源（EventStore + reduce）与 Fork 分支执行

日期: 2026-07-31  状态: 已采纳

## 决策

**(A) 事件溯源**：不重命名/不删除任何现有事件类型（grep 全仓库确认只有 3 处
真实订阅者：`run_eval.py` 订 `tool.after`，`run_langgraph_real.py` 订
`tool.approval`，`run_observability_real.py` 订 `*`，重命名会白白破坏 2/3）。
在 `kernel.py` 的 9 个状态变更点新增同名新事件（`run.started`/`tool.proposed`/
`run.paused`/`tool.approved`/`tool.started`/`tool.completed`/`run.resumed`/
`run.completed`/`run.failed`），跟现有事件并存发布。`run.failed` 是原始
vocabulary 没写、设计过程中确认必须补的——`reduce()` 要同等覆盖 done/failed
两种终态，否则退化成字符串嗅探现有的 `run.end` payload。

`message.appended` 不做成独立事件：`tool.completed` 携带 `_finish_tool` 里
已经序列化好的 `assistant_message` 字符串（复用同一份 `json.dumps` 结果，
不在 `reduce()` 里重新拼一遍），`run.started`/`run.completed` 自带完整消息
内容，四个 `state.add()` 位置由三个更高层事件覆盖。

`model.requested`/`model.completed` 不做：内核对单次模型调用没有可见性
（`ObservedModel` 的活，可选独立），`RunState` 没有字段要靠它们重建。

**不新增 `EventStore` port**：`SqliteEventStore`（`adapters/event_store.py`）
是纯 `EventBus` 订阅者，风格照抄 `adapters/effects.py`（建表、`INSERT`、
`load_events(run_id)` 查询、`close()`/上下文管理器），跟 `JsonlEventRecorder`/
`CostLedger` 一样没有 port——`ports.py` 只在内核真正依赖时才加抽象，内核从不
依赖事件账本。`reduce()`（`src/agent_kernel/event_sourcing.py`）是纯标准库
函数，按事件类型重放出等价的 `RunState`。

**(B) Fork**：建在 `JsonCheckpointStore` 已有的逐 `(turn, step)` 快照文件上
（`turn_NNN_step_NNN.json`，`evals/run_m3.py` 早就在断言这个文件存在），**不
依赖 (A)**。新增只读方法 `load_step(run_id, turn, step)`；`fork()`
（`src/agent_kernel/fork.py`，一个函数不是类）读取指定历史快照、序列化往返
做深拷贝、换新 `run_id`、记 `forked_from` 字段，不写盘。`kernel.py` 零改动——
fork 出来的 `RunState` 直接交给现有 `AgentKernel(...).resume()` 就能跑。

**不做 `state_patch`**：用户原句给的 API 是
`fork(..., state_patch={"approved": False})`，`RunState` 没有 `approved`
字段，通用任意字段 patch 有把状态改出内部不一致的风险（比如只改 `status`
不同步改 `pending_tool`）。"批准分支 vs 拒绝分支" demo 效果改用同一份 fork
出来的 `RunState` 分别交给两个用不同 `approval=` 回调构造的 `AgentKernel`
各自 `resume()`，零新增 patch 接口面就能 100% 达到同样效果。

## 原因

用户想要审计日志、状态重建、分支执行、eval 重放、事故分析这些能力，同时明确
选择了加法路线——`EventStore` 跟 `CheckpointStore` 并存，不拆现有的
checkpoint-based resume 路径。这延续了 Effect Ledger（ADR-0008）确立的模式：
新能力默认不启用时代码路径全部死代码，零回归风险。

## 如实声明边界

- `SqliteEventStore` 继承 `EventBus.publish` 现有的 `except Exception: pass`
  行为——这是 **best-effort，不是 exactly-once**，跟 `JsonlEventRecorder` 同一
  可靠性等级。审计日志可能因为订阅者故障静默丢事件；`resume()` 权威数据源
  仍然是 `CheckpointStore`，不是事件账本。
- `reduce()` 重建不出 `context_summary`/`summarized_message_count`——这两个
  字段是 `ContextBuilder`（planner 侧）改的，不在内核状态变更点里。验证 eval
  用短场景，不触发摘要阈值，避免在范围外字段上断言相等。
- **一个真实的时序坑**：工具调用这一步会在同一个 `(turn, step)` 写两次
  checkpoint——`_drive()` 循环体刚把状态设成 `paused` 时一次，同一次同步调用
  里 `_run_pending_tool` 马上解决审批后又一次（`os.replace` 直接覆盖）。脚本
  化场景里天然不存在"还没决定、又durably落盘"的 paused 快照可以 fork，除非
  approval 回调本身阻塞或崩溃。Demo/eval 复用 `evals/run_effects.py`
  已经建立的"approval 回调 `raise SystemExit` 模拟崩溃"手法拿到 fork 点——这
  是唯一现实可行、非交互式可复现的方式，不是额外发明的复杂度。

## 范围外

- RunState 纯投影的彻底重构（用户这轮明确选了加法路线）。
- `model.requested`/`model.completed` 事件。
- 通用任意字段 `state_patch`（被两个不同 `approval` 回调的 kernel 实例替代）。
- fork 分支不拷贝源 run 的 Effect Ledger/EventStore 历史行——`effect_id`/
  事件都是 `run_id` scoped，新分支自然从新 `run_id` 下重新累积，跟源 run 的
  历史无关。
- 任何 UI/CLI 调试器、time travel 浏览工具——这些是"有了事件账本之后能做的
  事"，不是这轮交付物；交付物是 eval 和 demo 脚本。

## 验证

`evals/run_event_sourcing.py`：3 个场景（工具调用+批准+终答、+拒绝+终答、
步数耗尽失败），每个断言 `reduce(store.load_events(run_id))` 与真实
`checkpoints.load(run_id)` 逐字段相等（`messages`/`status`/`answer`/
`pending_tool`/`pending_effect_id`/`step`）。

`evals/run_fork.py`：`raise SystemExit` 模拟崩溃拿到 fork 点 → fork 两次 →
两个不同 `approval` 回调各自 `resume()` → 断言批准分支工具真的执行了一次、
拒绝分支工具一次都没执行、两条分支答案不同、源 run 的 checkpoint 在整个过程
中完全没变。

全量回归（`run_eval.py --mode offline`/`run_m3.py context`/`run_m5.py`/
`run_composite_memory.py`/`run_preferences.py`/`run_consolidation.py --mode
offline`/`run_effects.py`/`run_observability_real.py`/`run_langgraph_real.py`/
`run_worker_real.py`/`examples/demo_ops.py`/`record_demos.py --check`/
`compileall`/`check_core_imports.py`）确认新增的 9 个 `_emit` 调用在没有
订阅者时零行为影响，`record_demos.py --check` 的字节级 diff 确认确定性录制
输出完全不变。
