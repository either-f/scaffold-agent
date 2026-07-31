# ADR-0007: M4-M6 技能/沙箱/观测/多 Agent 决策补录

日期: 2026-07-31（补录，原决策落地于 2026-07-27 ~ 2026-07-29）  状态: 已采纳

补录说明：M4-M6 落地时未同步写 ADR，违反了融合纪律第 2 条（"每引入一个借鉴点，写一条
ADR"）。本篇按 PLAN.md 里已记录的决策与坦诚声明补齐，不改变任何既有实现。

## M4：Skill 与沙箱

- `SkillLoaderPort` 走 Anthropic Agent Skills 规范（SKILL.md + 渐进披露）：`list_tools`
  只暴露 name+description，正文经 `load_skill` 按需加载，避免技能库增大直接撑爆 prompt。
- 沙箱选 Docker（本地起步），用 `--read-only --network none --cap-drop ALL
  --security-opt no-new-privileges` 锁定，而非直接对齐 E2B 云沙箱 API——先用最小本地方案
  验证 CodeAct 策略可行，接口设计上留出替换空间（`SandboxExecutor`）。
- 坦诚声明（已解决，2026-07-31）：此前安全参数只经离线 demo 注入 runner 验证结构，
  未启动真实容器做隔离穿透测试。现已在真实 Docker daemon（远程主机，本机 Docker Desktop
  当时未启动）上逐字复用 `DockerSandbox.build_command()` 跑通 8 项隔离验证：只读文件系统
  拒绝写入、`/tmp` 可写、出网被拒绝、看不到宿主挂载路径、`pids.max`/`memory.max` cgroup
  限额与配置一致、`NoNewPrivs=1`。过程中发现并修复一个真实 bug：`execute()` 传给本地
  `subprocess.run` 的 `env` 只含 `_AKC`、不含 `PATH`，docker 装在 POSIX 默认路径之外会
  找不到可执行文件；已改成继承宿主环境再叠加 `_AKC`，容器内隔离边界不受影响（容器只会
  拿到 `-e _AKC` 点名的这一个变量）。见 [evals/baseline-m4-sandbox-real.json](../../evals/baseline-m4-sandbox-real.json)。

## M5：观测与评测

- `EventBus` 发布/订阅解耦观测：`ObservedModel`/`CostLedger`/`JsonlEventRecorder`/
  `OtelExporter`/`LangfuseExporter` 全部通过订阅事件实现，不侵入内核调用点，直接复用
  ADR-0001 定的横切件设计。
- 4 策略（ReAct/Plan-Execute/CodeAct/LangGraph）用同一套离线 eval 对比，复用 M2 的
  `run_task` 评测框架结构，不另起一套评测协议。
- 坦诚声明（已解决，2026-07-31）：此前 LangGraphPlanner 只用注入的假图做集成验证，未编译
  真实 LangGraph StateGraph。现已在 `adapters/langgraph_demo_graph.py` 编译一个真正的
  4 节点 + 条件边 StateGraph 喂给 `LangGraphPlanner`，final/tool/HITL 三条真实分支全部
  跑通；`LangGraphPlanner` 本身接口零改动，因为它本来就只依赖 `graph.invoke(dict)->dict`
  这个薄契约，验证了 ADR-0001 里"新概念接入=新 adapter，内核零改动"的设计承诺。
- 坦诚声明（OTel 部分已解决，2026-07-31）：此前 OTel/Langfuse 只提供 adapter，无真实
  SDK 验证。现已用真实 `opentelemetry-sdk` + `InMemorySpanExporter` 验证全部 6 类事件
  正确生成 span；过程中发现并修复一个真实 bug——`tool.before` 的 `args` dict 不满足
  OTel attribute 的标量类型要求，SDK 静默丢弃该字段，调用参数从未真正进过 trace，现在
  序列化成 JSON 字符串再塞进 span。Langfuse self-host/Cloud 实时重放仍未验证：没有现成
  实例，自建 self-host 需要 Postgres+ClickHouse+Redis，成本明显高于这轮其它验证项。

## M6：多 Agent 与互操作

- Worker 委派用 orchestrator-worker 模式（借鉴 Claude Agent SDK 的子 agent 思想），
  实现为 `WorkerDelegationPort(ToolPort)` 装饰器——worker 本身是完整 `AgentKernel`，
  对父 kernel 只暴露一个 `worker_<name>` 工具，内核零改动即可递归组合。
- A2A 互操作没有引入官方 `a2a-python` SDK，改用标准库 `http.server` 实现最小 Agent Card
  + task handler：官方 SDK 当时仍在快速演进，先用协议形态兼容，换 SDK 只影响
  `adapters/interop_a2a.py` 一个文件。
- 图记忆最初只用 SQLite 存三元组（`adapters/memory_graph.py`），只为验证 `MemoryPort`
  能否零改动扩展到图数据库范式；这条已在 2026-07-30 按 [ADR-0006](0006-graph-memory-neo4j.md)
  换成真实 Neo4j 生产 adapter，SQLite 版本降级为 Fake。
- 坦诚声明（已部分解决）：Worker 委派此前 demo 只用 FakeScriptedModel 验证，2026-07-31
  已用 `evals/run_worker_real.py` 跑通真实 DeepSeek 双层调用，见 README M6 章节。
  A2A 仍非官方 SDK，未做完整协议握手（task 状态推送、streaming、认证）；这部分成本
  明显高于当前收益，暂不推进。

## 后果与迁移条件

- 后续任何借鉴点落地都必须当次写 ADR，不再补录——CI 目前不强制检查这条，靠人工纪律。
- 沙箱真实容器验证、LangGraph 真实 StateGraph、A2A 官方 SDK 三项，出现真实需求
  （比如要接第三方 agent 网络、要跑不受信代码）时再单独立项，不在本轮推进范围。
