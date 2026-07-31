# agent-kernel

微内核 + 插件端口的通用 agent 框架。内核零第三方依赖；外部生态只进入
`adapters/` 与 `planners/`。

## M0：离线运行

```powershell
$env:PYTHONPATH = "src"
python examples/run_demo.py
```

## M1：DeepSeek + MCP + LangChain

本机无需另装 Python；使用 Python 3.14，并把工具、虚拟环境与缓存都留在项目内：

```powershell
python -m pip install --prefix .tools/uv uv==0.11.29
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.venv"
.tools\uv\Scripts\uv.exe sync --extra model --extra mcp --extra langchain
```

设置密钥并运行真实链路：

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
.tools\uv\Scripts\uv.exe run python examples/run_demo.py --m1
```

M1 会启动两个 stdio MCP server：

- Filesystem：仅允许访问当前 `agent-kernel/`，客户端白名单只暴露读取与搜索工具。
- Fetch：演示参数 guard 只允许 `https://example.com`。

Windows 上 Filesystem 通过 `cmd /c npx` 启动，固定版本
`@modelcontextprotocol/server-filesystem@2026.7.10`；npm 缓存写入 `.cache/npm/`。
MCP Python SDK 固定 `<2`，暂不采用仍在迁移期的 v2。

LangChain 只提供 `BaseChatModel → ModelPort` 和 `BaseTool → ToolPort` 兼容层，
不把 LangChain Agent/Chain 运行时放进内核。LangGraph 留到 M5 的复杂工作流评测。

## M2：恢复、审批与 Eval

M1 真实链路现在会在 `runs/` 写入原子 checkpoint。启用逐工具 CLI 审批：

```powershell
.tools\uv\Scripts\uv.exe run python examples/run_demo.py --m1 --hitl
```

若在审批提示或工具执行期间中断，事件流中的 `run_id` 可用于恢复：

```powershell
.tools\uv\Scripts\uv.exe run python examples/run_demo.py --m1 --resume RUN_ID --hitl
```

恢复不会重复添加用户消息；待审批工具会先重新审批。工具执行采用 at-least-once：
若外部工具已完成但结果尚未 checkpoint，恢复可能再次调用，因此自动重试只对只读工具开放。

运行不需要密钥或网络的离线评测：

```powershell
.venv\Scripts\python.exe evals\run_eval.py --mode offline
```

生成真实 DeepSeek 基线：

```powershell
.venv\Scripts\python.exe evals\run_eval.py --mode deepseek --output evals/baseline-m2.json
```

离线 eval 必须通过 `10/10`；真实 DeepSeek 基线至少通过 `8/10`。CI 只运行离线门禁，
不会读取模型密钥或启动 MCP server。

M2 基线（2026-07-27）：

| 模式 | 模型 | 通过率 | 平均步数 |
|---|---|---:|---:|
| 离线门禁 | FakeScriptedModel | 10/10 | 2.1 |
| 真实链路 | deepseek/deepseek-chat | 9/10 | 2.1 |

真实逐题结果见 [evals/baseline-m2.json](evals/baseline-m2.json)。

## M3A：PostgreSQL + pgvector 语义记忆

启动项目固定版本的 pgvector 数据库，并安装记忆依赖：

```powershell
docker compose up -d postgres
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.venv"
.tools\uv\Scripts\uv.exe sync --extra model --extra memory
```

默认连接为 `postgresql://agent:agent@127.0.0.1:5432/agent_memory`，可通过
`AGENT_MEMORY_DSN` 覆盖。设置现有 DashScope 密钥后运行真实语义检索验收：

```powershell
$env:DASHSCOPE_API_KEY = "你的密钥"
.venv\Scripts\python.exe evals\run_m3.py memory --output evals/baseline-m3.json
```

`PgVectorMemory` 只保存 user 和最终 assistant 原消息，以构造参数中的 namespace 隔离；
tool 结果不会进入长期记忆。首版使用 `text-embedding-v4` 的 1024 维向量和精确余弦检索，
不做事实抽取、HNSW 或连接池。

M3A 基线（2026-07-27）：语义查询 `5/5`，去重、tool 排除和 namespace 隔离均通过。
完整结果见 [evals/baseline-m3.json](evals/baseline-m3.json)。

## M3B：有界多轮上下文

已完成的 `RunState` 可以用同一 `run_id` 继续调用 `AgentKernel.run()`；每轮 step 重新计数，
checkpoint 按 `turn_<NNN>_step_<NNN>.json` 保存，旧 checkpoint 仍可加载。
`ReactPlanner` 的默认 `ContextBuilder` 在约 24,000 字符时把较早历史增量压缩为不超过
2,000 字符的摘要，同时保留 `RunState.messages` 完整审计记录。`OffloadingToolbox` 会把超过
8,000 字符的工具结果原子写入 `runs/artifacts/<sha256>.txt`，只把路径、长度和首尾预览回传模型。

离线运行 50 轮上下文与 artifact 门禁：

```powershell
.venv\Scripts\python.exe evals\run_m3.py context
```

数据库、DashScope、DeepSeek 均就绪后运行全部 M3 验收并刷新基线：

```powershell
.venv\Scripts\python.exe evals\run_m3.py all --output evals/baseline-m3.json
```

M3B 离线基线（2026-07-27）：50/50 轮完成，增量摘要 2 次，最大 ReAct prompt
23,342 字符；多轮 checkpoint 与 artifact 完整回读均通过。真实跨会话验收中，第二个
DeepSeek 会话成功从 pgvector 回答项目代号 `Mercury` 与中文偏好；完整结果见
[evals/baseline-m3.json](evals/baseline-m3.json)。

## CompositeMemory：统一封装分层记忆

`adapters/memory_composite.py` 的 `CompositeMemory(MemoryPort)` 把短期情景记忆（全量
最近消息，如 `SqliteMemory`）、长期语义记忆（提炼后的事实，如 `PgVectorMemory`）、
可选图谱记忆（`GraphMemory` / `Neo4jGraphMemory`）统一封装成一个 `MemoryPort`，对
`AgentKernel` 和 `Planner` 完全透明——它们只看到标准的 `add/search`。

```python
memory = CompositeMemory(
    episodic=SqliteMemory("runs/episodic.db"),
    semantic=PgVectorMemory(dsn, "long-term"),
    graph=Neo4jGraphMemory(uri, user, password, "graph"),  # 可选
)
```

- 写：只落 episodic，不重复写 semantic——长期语义记忆只该存"提炼后的事实"，不是每条
  原始消息，运行时全量写入违反 ADR-0004 的既定边界；提炼交给
  [离线记忆巩固](#离线记忆巩固)。
- 查：semantic（已提炼知识，优先级最高）→ graph（关系型证据，若配置）→ episodic
  （原始细节，兜底）三路合并去重返回，保证"长期沉淀"排在"原始细节"前面。
- 内核零改动：`CompositeMemory` 就是一个新 adapter，`kernel.py`/`ports.py` 没有变化。

```powershell
.venv\Scripts\python.exe evals\run_composite_memory.py
```

## 偏好与约束记忆

`ReactPlanner` 支持一个独立于常规记忆的 `preferences: MemoryPort`：常规记忆按当前用户输入
做相关性检索，偏好记忆固定查询"用户的语言习惯、格式要求、风格偏好、禁忌与约束"，
每轮无条件注入 system prompt，不受当前问题是否相关影响。

```python
preferences = PgVectorMemory(dsn, namespace="preferences")
planner = ReactPlanner(preferences=preferences)
```

只做读取注入；把偏好陈述自动识别并写入 `namespace="preferences"` 属于离线记忆巩固的范围，
目前需要手动调用 `preferences.add(run_id, "user", "以后回复都用中文")` 写入。

```powershell
.venv\Scripts\python.exe evals\run_preferences.py
```

## 离线记忆巩固

独立离线脚本 `evals/run_consolidation.py`：读 `CheckpointStore` 里的短期情景记忆
（`RunState.messages` 完整时序，M3B 已落地），用 LLM 抽取事实与偏好，分别写入长期语义
记忆和 `namespace="preferences"` 偏好记忆——去重、合并、提取事实、修正错误，模拟
"闲时整理记忆"。只通过 `MemoryPort.add/search` 读写，不侵入运行时内核；`CheckpointStore`
只读。

```powershell
# 离线逻辑校验（Fake 模型 + 内存字典，CI 用）
.venv\Scripts\python.exe evals\run_consolidation.py --mode offline

# 真实链路：真实 DeepSeek 对话生成 checkpoint → 真实 DeepSeek 抽取 → 真实 pgvector 写入/检索
$env:DEEPSEEK_API_KEY = "你的密钥"
$env:DASHSCOPE_API_KEY = "你的密钥"
$env:AGENT_MEMORY_DSN = "postgresql://agent:agent@127.0.0.1:5432/agent_memory"
.venv\Scripts\python.exe evals\run_consolidation.py --mode real --output evals/baseline-consolidation-real.json
```

真实基线（2026-07-31）：2 个 run 全部处理，事实与偏好各抽取 1 条，写入后能被真实语义检索
命中，见 [evals/baseline-consolidation-real.json](evals/baseline-consolidation-real.json)。

坦诚声明：MemoryPort 首版不提供 update/delete（ADR-0004），"修正错误"做不到覆盖旧记录，
只是让抽取 prompt 只输出修正后的最新结论、不重复写入过时旧结论；各 adapter 检索按时间
倒序，新结论排在旧结论前面——这是"软修正"，不是真删除。

## M4：Skill 与沙箱

渐进式技能系统采用 Anthropic Agent Skills 规范（SKILL.md + 渐进披露）。技能工具
默认只披露 name+description，正文经 `load_skill` 工具按需加载。新增技能 =
往 `skills_library/` 放一个文件夹，零代码变更。

Docker 沙箱执行器通过 `--read-only --network none --cap-drop ALL --security-opt no-new-privileges`
锁定容器；`SandboxToolbox` 暴露唯一的 `python_execute` 工具供 CodeAct 策略使用。

真实容器隔离验证（2026-07-31）：在真实 Docker daemon 上跑 `DockerSandbox.build_command()`
构造的命令（逐字复用，非重写），8 项全部通过——写宿主文件被拒绝、`/tmp` 可写、
出网被拒绝、看不到宿主挂载路径、`pids.max`/`memory.max` cgroup 限额与配置一致、
`NoNewPrivs=1`。结果见 [evals/baseline-m4-sandbox-real.json](evals/baseline-m4-sandbox-real.json)。
验证过程中发现并修复一个真实 bug：`execute()` 传给本地 `subprocess.run` 的 `env` 之前只有
`_AKC`、没有 `PATH`，docker 装在 POSIX 默认路径之外时会找不到可执行文件；现在改成继承
宿主环境再叠加 `_AKC`，容器内仍只会拿到 `-e _AKC` 点名的这一个变量，隔离边界不受影响。

## M5：观测与评测

事件总线（EventBus）发布/订阅模式支持外部观测器无侵入接入：
`ObservedModel` 发布 `model.complete` 事件、`CostLedger` 统计 token 费用、
`JsonlEventRecorder` 写事件回放日志、`OtelExporter` 与 `LangfuseExporter`
分别对接 OpenTelemetry 和 Langfuse。

策略对比（离线，4 策略 × 3 用例）：

```powershell
.venv\Scripts\python.exe evals\run_m5.py
```

M5 策略离线基线：

| 策略 | 通过率 | 平均步数 | 备注 |
|------|:---:|:---:|------|
| ReAct | 3/3 | 2.33 | JSON 动作协议，无计划开销 |
| Plan-Execute | 3/3 | 2.00 | 首步额外生成计划 |
| CodeAct | 3/3 | 2.00 | 沙箱执行，安全隔离 |
| LangGraph | 3/3 | 1.67 | 图适配，含 HITL 形模拟 |

真实 OpenTelemetry 验证（2026-07-31）：用真实 `opentelemetry-sdk`（非 mock）+
`InMemorySpanExporter` 跑一次完整 run，8 个 span 全部生成（`run.start`/`step.start`×2/
`model.complete`×2/`tool.before`/`tool.after`/`run.end`），token 用量与耗时属性都在。
验证过程中发现并修复一个真实 bug：`tool.before` 的 `args` 是 dict，OTel span attribute
只接受标量或同类序列，SDK 会静默丢弃这个字段（只打 warning，外层 try/except 完全捕不到）——
调用参数从来没真正进过 trace；现在 `OtelExporter` 把非标量 payload 值序列化成 JSON 字符串
再塞进 span，已验证 `args` 确实被捕获。见
[evals/baseline-m5-otel-real.json](evals/baseline-m5-otel-real.json)。

```powershell
.venv\Scripts\python.exe evals\run_observability_real.py
```

Langfuse 仍未验证：`LangfuseExporter` 把事件 metadata 整体转发（Langfuse 的 metadata 是
任意 JSON，不受 OTel 那种标量属性限制，理论上没有同类问题），但需要一个 self-host 或
Langfuse Cloud 实例才能验证真实 trace 回放；本地/远程都没有现成的 Langfuse 服务，暂不推进——
自建 self-host 至少要 Postgres + ClickHouse + Redis，比这轮其它验证重得多。

真实 LangGraph StateGraph 验证（2026-07-31）：`adapters/langgraph_demo_graph.py` 编译一个
真正的 `langgraph.graph.StateGraph`（4 节点 + 条件边：classify 判断走 tool / 已有工具结果
的 final / 无需工具的 final，`add_conditional_edges` 真实路由），喂给 `LangGraphPlanner`，
不是回放脚本化输出。final / tool / HITL 三条分支全部真实跑通，HITL 分支验证真实图产出的
`ToolCall` 会触发内核 `approval` 回调（同步拦截，非模拟）。见
[evals/baseline-m5-langgraph-real.json](evals/baseline-m5-langgraph-real.json)。

```powershell
.venv\Scripts\python.exe evals\run_langgraph_real.py
```

完整报告见 [evals/baseline-m5.json](evals/baseline-m5.json)。

## M6：多 Agent 与互操作

Worker 委派通过 `WorkerDelegationPort` 实现 orchestrator-worker 模式；
A2A 互操作通过 `A2AInteropAdapter` + `create_a2a_server` 暴露 Agent Card 与 HTTP task handler；
图状记忆现有两个 adapter：`GraphMemory`（SQLite，离线 Fake，CI 用）与
`Neo4jGraphMemory`（真实 Neo4j，生产 adapter），均以实体-关系-实体三元组验证
MemoryPort 抽象不需改内核即可扩展到图数据库范式。

启动图数据库并跑真实验收：

```powershell
docker compose up -d neo4j
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.venv"
.tools\uv\Scripts\uv.exe sync --extra graph
.venv\Scripts\python.exe evals\run_graph.py --output evals/baseline-m6-graph.json
```

默认连接 `bolt://127.0.0.1:7687`，账号密码可用 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`
覆盖。详见 [ADR-0006](docs/adr/0006-graph-memory-neo4j.md)。

Worker 委派的真实生产 LLM 验证（此前 demo 只用 FakeScriptedModel）：

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
.venv\Scripts\python.exe evals\run_worker_real.py --output evals/baseline-m6-worker.json
```

M6 真实基线（2026-07-31）：图记忆 8/8（真实 Neo4j，见 [evals/baseline-m6-graph.json](evals/baseline-m6-graph.json)），
Worker 委派真实 DeepSeek 双层调用通过（见 [evals/baseline-m6-worker.json](evals/baseline-m6-worker.json)）。

## M7：Demo 录制与求职物料

三个离线演示场景 + asciicast v2 录制器：

```powershell
.venv\Scripts\python.exe examples\record_demos.py          # 录制全部 .cast
.venv\Scripts\python.exe examples\record_demos.py --check  # CI 离线验证
.venv\Scripts\python.exe examples\record_demos.py --play research  # 回放
```

演示回放与简历要点见 [docs/demos/](docs/demos/) 和 [docs/resume-bullets.md](docs/resume-bullets.md)。

## 回归门禁

```powershell
.venv\Scripts\python.exe evals\run_eval.py --mode offline
.venv\Scripts\python.exe evals\run_m3.py context
.venv\Scripts\python.exe evals\run_m5.py
.venv\Scripts\python.exe evals\run_composite_memory.py
.venv\Scripts\python.exe evals\run_preferences.py
.venv\Scripts\python.exe evals\run_consolidation.py --mode offline
.venv\Scripts\python.exe evals\run_observability_real.py  # 需要 --extra obs（真实 opentelemetry-sdk，非 mock）
.venv\Scripts\python.exe evals\run_langgraph_real.py  # 需要 --extra langgraph（真实编译 StateGraph）
.venv\Scripts\python.exe examples\record_demos.py --check
.venv\Scripts\python.exe -m compileall -q src examples evals
```

## 结果总览

| 里程碑 | 内容 | 状态 | 关键指标 |
|--------|------|:----:|------|
| M0 | 内核骨架 | ✅ | 纯标准库，离线可跑 |
| M1 | DeepSeek + MCP + LangChain | ✅ | 真实模型链路 |
| M2 | 恢复、审批与 Eval | ✅ | 离线 10/10，真模型 9/10 |
| M3 | 语义记忆 + 有界上下文 | ✅ | 语义检索 5/5，50 轮上下文 |
| M4 | Skill 与沙箱 | ✅ | 渐进披露 + Docker 沙箱（真实容器隔离验证 8/8） |
| M5 | 观测与策略对比 | ✅ | 4 策略 12/12 + 真实 OTel span + 真实 LangGraph StateGraph |
| M6 | 多 Agent 与互操作 | ✅ | Worker + A2A + 图记忆（真实 Neo4j） |
| M7 | 打包与求职物料 | ✅ | 3 个 demo + .cast + 简历 |

### 坦诚声明

- ~~**沙箱**：Docker 命令/安全构建经离线 demo 验证参数结构，未提供真实容器隔离证明。~~
  已解决（2026-07-31）：在真实 Docker daemon 上跑通 8 项隔离验证（只读文件系统、无网络、
  无宿主挂载、cgroup 限额、no-new-privileges），见 M4 章节与
  [evals/baseline-m4-sandbox-real.json](evals/baseline-m4-sandbox-real.json)。
- ~~**观测**：OTel/Langfuse 导出器仅提供 adapter，无 self-host Langfuse 实时重放证据。~~
  OTel 部分已解决（2026-07-31）：真实 `opentelemetry-sdk` 验证 8 个 span 全部生成，
  见 M5 章节。Langfuse self-host/Cloud 实时重放仍未验证——没有现成实例，自建 self-host
  基础设施（Postgres+ClickHouse+Redis）成本明显高于这轮其它验证项，暂不推进。
- ~~**LangGraph**：策略通过注入假图集成验证，未编译真实 StateGraph。~~ 已解决
  （2026-07-31）：`adapters/langgraph_demo_graph.py` 编译真实 `StateGraph`（4 节点 +
  条件边），`LangGraphPlanner` 驱动 final/tool/HITL 三条真实分支全部跑通，见 M5 章节。
- **A2A**：`A2AInteropAdapter` + `create_a2a_server` 为标准库 `http.server` 实现，非官方/完整 A2A SDK。
- ~~**Worker 委派**：demo 模型为 FakeScriptedModel，非生产 LLM。~~ 已解决（2026-07-31）：
  `evals/run_worker_real.py` 用真实 DeepSeek 驱动父子两层 AgentKernel，通过。demo 本身
  仍用 FakeScriptedModel 保持确定性录制，生产证据在 eval 里。

完整规划见 [PLAN.md](PLAN.md)。

## 扩展纪律

1. 第三方 import 只允许出现在 `adapters/` 与 `planners/`。
2. 每个真 adapter 必须有 Fake 对照，内核始终可离线运行。
3. 新依赖或借鉴点必须带 ADR 与真实运行证据。
