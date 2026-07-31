# ADR-0008: Effect Ledger——工具副作用一致性与可恢复执行

日期: 2026-07-31  状态: 已采纳

## 决策

- 新增第三个横切件 `EffectLedger`（`ports.py`），跟 `EventBus`/`CheckpointStore`
  并列；`AgentKernel` 新增可选构造参数 `effects: EffectLedger | None = None`，
  默认不传时行为与改动前逐字节相同（每个既有 eval/demo 都以 `effects=None`
  运行，全量回归零变化）。
- `effect_id` 由 `f"{run_id}:{step}"` 确定性生成，跟随 `RunState.pending_tool`
  一起持久化到 checkpoint 里的新字段 `pending_effect_id`。
- 生命周期 `proposed → approved → executing → succeeded/failed`：`executing`
  写在真正调用工具**之前**，`succeeded`/`failed`+`result_ref` 写在调用返回
  **之后**，同一进程内。
- 恢复时 `_run_pending_tool` 先查账本，不再无脑重跑 `pending_tool`：
  - `succeeded` → 直接回放 `result_ref`，不重复调用工具。
  - `executing`/`failed` 且工具 `ToolEffectPolicy.idempotent=True` 且未超
    `retry_policy.max_attempts` → 安全重试。
  - 否则 → 抛 `EffectUnresolvedError`（携带 `effect_id`/`run_id`/`tool_name`/
    `status`），由人工核实外部系统真实状态、手动调用
    `mark_succeeded`/`mark_failed` 修正账本后再 `resume()`。
  - `proposed` → 不在上述任何分支里，走原有审批路径，等价于"重新审批"。
- `SqliteEffectLedger`（`adapters/effects.py`）风格照抄 `observability.py` 的
  `CostLedger`：本机 sqlite3、`INSERT OR IGNORE` 幂等 propose、`UPDATE` 各状态
  转移，`mark_executing` 是唯一的 attempt 计数点。
- `ToolSpec` 新增可选字段 `effect_policy: ToolEffectPolicy | None`，工具自己声明
  是否幂等、重试上限；已确认 `react.py`/`langgraph.py` 的 prompt 构建都是显式取
  `name/description/parameters`，这个字段不会泄漏进模型看到的 prompt。

## 原因

这条 ADR-0003 早就写好了迁移条件："未来开放写入、移动或删除工具前，必须增加
幂等键或调用日志；这些工具默认不得自动重试。" 本次改动就是兑现这个条件——
checkpoint 只解决了"状态能恢复"，没解决"副作用不重复"：工具在
`self.tools.call(...)` 成功后、`_finish_tool` 末尾的 checkpoint 落盘前，进程
崩溃，恢复后会重新调用同一个工具（发邮件、建订单、转账都可能被执行两次）。

**如实声明边界**：这套机制不是分布式 exactly-once。ledger 写入 `executing`
和真正的外部调用不是原子的，两者之间仍有极窄的崩溃窗口——但这个机制把"完全
无法察觉的重复调用"变成了"恢复时可检测的 executing/unknown 状态"，内核据此
拒绝对非幂等工具的自动重试，而不是像今天这样盲目重跑。要做到真正的
exactly-once，需要外部系统自身支持幂等键（`Effect.idempotency_key` 字段已经
生成并记录，格式 `f"effect:{effect_id}"`，但本轮不会自动注入进
`action.args`——没有具体工具消费方之前，改动模型选的参数字典风险大于收益）。

## 范围外（明确不做，防止蔓延）

- 不做跨进程 2PC / 外部系统自动核对；`EffectUnresolvedError` 之后没有配套的
  自动化解决 CLI，只能人工介入。
- 不自动把 `idempotency_key` 注入 `action.args`。
- 不做指数退避/抖动，`RetryPolicy.max_attempts` 只是整数上限。
- `ToolEffectPolicy.compensatable`/`requires_approval` 是记录但不生效的元数据
  （用户明确要求过这两个字段，保留但如实标注"暂未接入内核逻辑"）；审批门控
  仍然只看 `AgentKernel.approval` 是否配置。
- 没有接入任何现有 demo（`examples/*.py` 全部保持 `effects=None`），把这轮改动
  的爆炸半径压到最小；接入是后续单独的小改动。
- 不做账本清理/GC；不做并发 resume 加锁（ADR-0003 已经禁止并发 resume 同一
  `run_id`，这条不变）。

## 验证

`evals/run_effects.py` 三个场景，用 `raise SystemExit`（`BaseException`，
`_execute_tool` 的 `except Exception` 抓不住）在工具产生副作用后立刻中断，
两个独立 `AgentKernel` 实例分别代表崩溃前/恢复后进程，共享同一份磁盘上的
checkpoint + ledger 文件：
1. 非幂等工具停在 `executing` → `resume()` 拒绝重试（`EffectUnresolvedError`），
   副作用计数器没有增加第二次。
2. 幂等工具（`max_attempts=2`）停在 `executing` → `resume()` 安全自动重试一次，
   跑完，账本 `attempt==2`。
3. 工具已成功、`succeeded` 写进账本、但 `_finish_tool` 的 checkpoint 还没落盘
   （对应最初 bug 报告的确切窗口）→ `resume()` 直接回放 `result_ref`，副作用
   计数器全程只有一次——这是本次改动要修的那个洞，也是最核心的一条断言。

全量回归（`run_eval.py --mode offline`/`run_m3.py context`/`run_m5.py`/
`run_composite_memory.py`/`run_preferences.py`/`run_consolidation.py --mode
offline`/`run_observability_real.py`/`run_langgraph_real.py`/
`run_worker_real.py`/`examples/demo_ops.py`/`record_demos.py --check`/
`compileall`/`check_core_imports.py`）在 `effects=None` 下全部通过，结果与
改动前一致。

## 勘误（2026-07-31）

上面「决策」第 11 条写的 `effect_id = f"{run_id}:{step}"` 有真实 bug：多轮
对话场景下 `AgentKernel.run()` 二次调用会把 `RunState.step` 清零重新计数
（见 `kernel.py` 的 `run()`），但 `run_id` 不变——于是第二轮第一步工具调用
生成的 `effect_id` 会跟第一轮第一步撞车，命中账本里 `succeeded` 记录后直接
把第一轮的结果回放给第二轮参数完全不同的调用，是数据错误级问题，不是本
ADR 讨论过的"极窄崩溃窗口"那类已知边界。

修复：`effect_id` 改为 `f"{run_id}:{turn}:{step}"`；并在 `_run_pending_tool` 命中账本记录时新增
`arguments_hash` 校验——跟本次调用的哈希对不上就抛新增的
`EffectArgumentMismatchError`，拒绝回放/重试，而不是本 ADR 原先假设的
"同一个 effect_id 一定对应同一次调用"。回归测试见
`tests/test_effects.py::test_effect_id_scoped_by_turn_not_just_step` /
`test_argument_hash_mismatch_raises`。
