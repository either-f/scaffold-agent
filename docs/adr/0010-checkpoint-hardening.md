# ADR-0010: Checkpoint 加固——run_id 校验、并发写检测、schema 版本化

日期: 2026-08-03  状态: 已采纳

## 决策

**(A) run_id 校验**：在 `RunState.__post_init__` 加一个 deny-list 友好的正则
`^[A-Za-z0-9_.\-]{1,128}$`，额外显式拒绝 `/` `\` `\x00` 和 `.` / `..` 整段路径
成分。这是 `RunState` 构造的唯一 choke point，覆盖 `AgentKernel.run(state=...)`、
`fork()`、以及测试/eval 里所有 `RunState(run_id=...)` 直接构造。现有可读 id
（`"scn-1"`、`"turn-scope"`、`"hitl-1"`、`"fork-approve"`、uuid hex）全部继续
通过；只挡住路径逃逸/注入字符。`run_id` 在进入 `Path(root) / state.run_id` 之前
就被 `ValueError` 拦住。

**(B) 并发写检测（乐观并发控制 / CAS-lite）**：`RunState` 新增 `revision: int = 0`
字段；`JsonCheckpointStore.save` 写前读 `latest.json` 的 `revision`，若磁盘版本
高于内存 `state.revision` 即抛 `CheckpointConflictError`，不静默覆盖更新的
checkpoint。`save` 成功时 `state.revision += 1`。

**不加文件锁**（`fcntl`/`msvcrt` 平台相关代码）。这是 CAS-lite 检测不是锁，
读-比-写窗口理论上仍有竞态，但把常见的"同一旧状态 resume 两次"从静默丢数据
变成可检测错误——满足 SPEC-86 的验收标准（两个 stale resume 第二个必须报错
而非静默覆盖）。

**(C) Schema 版本化**：`RunState.to_dict` 输出里加 `schema_version: int` 字面量
（`SCHEMA_VERSION = 2`），`from_dict` 按缺失/老版本号分支做就地兼容修复。
版本历史：
- **v1**：早期隐式形状（无 `schema_version` 字段；`turn` 可能为 0）。
- **v2**：当前形状（显式 `schema_version=2` + `revision` 字段）。

`from_dict` 缺失 `schema_version` 默认为 v1，对 v1 且 `turn == 0` 的 checkpoint
把 `turn` 抬到 1——把原本散在 `kernel.py::resume` 里的 ad hoc "兼容 M2 checkpoint"
shim 集中到 `from_dict` 一处。新增字段时在此 bump 版本号并补分支，而不是再散落
新的 ad hoc shim。

**(D) Fork 的 revision 语义**：`fork()` 把 forked copy 的 `revision` 重置为 0。
fork 是新谱系（新 `run_id`），其 revision 计数器与源 run 独立——源 run 的
revision 不应被 fork 带过来。

## 原因

`run_id` 今天内部都由 `uuid.uuid4().hex[:12]` 生成，但 `AgentKernel.run(state=...)`
已接受任意调用方字符串（测试/eval 传 `"scn-1"` 等），未来 A2A task id 或 CLI
resume 目标若流入 `run_id` 就是路径逃逸面。在 choke point 校验比在每个 `Path`
构造点防御更可靠。

并发写：SPEC-80 的 fork 分支场景和两个进程跑同一 run 都是真实并发写风险。完全
的并发安全需要文件锁或 DB-backed store（显式 out of scope），但乐观并发控制用
最小改动把"静默丢数据"变成"可检测错误"，性价比最高。

schema 版本化：`resume()` 里 `if state.turn == 0: state.turn = 1` 的 ad hoc shim
证明项目已经被无版本 checkpoint 咬过一次。加正式版本字段 + 集中迁移点避免下次
加字段时再散落新 shim。

## 后果与迁移条件

- 老 checkpoint 文件（无 `schema_version`/`revision`）经 `from_dict` 默认 v1 仍可加载。
- `CheckpointConflictError` 是 `RuntimeError` 子类，调用方需在并发场景捕获。
- 加文件锁或迁移到 DB-backed store（SPEC-86 明确 out of scope）是后续可选增强，
  接口不变。
- 新增 `RunState` 字段时 bump `SCHEMA_VERSION` 并在 `from_dict` 补 v1→v2 风格的
  兼容分支，不再加散落的 ad hoc shim。

## 验证

`tests/test_checkpoint.py`：
- `run_id` 含 `../../etc` / Windows 路径分隔符 / `.` / `..` / null 字节在
  `RunState` 构造时即 `ValueError`，到不了 `Path` 构造。
- 加载无 `schema_version`/`revision` 的老 checkpoint 正常工作，`turn==0` 被抬到 1。
- 两次 `save`，第二次用第一次 save 前（未递增）的 `state.revision` 模拟 stale
  resume，第二次 `save` 抛 `CheckpointConflictError`。

`evals/run_fork.py`、`evals/run_effects.py`、`pytest tests/ -q`、`compileall` 全量回归。
