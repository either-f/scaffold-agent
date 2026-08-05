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

恢复不会重复添加用户消息；待审批工具会先重新审批。工具执行**不再是无条件
at-least-once**——2026-07-31 起接入 [Effect Ledger](#effect-ledger工具副作用一致性)，
未配置时行为不变，配置后能检测并拒绝对非幂等工具的重复调用。

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

## Effect Ledger：工具副作用一致性

checkpoint 只保证**状态**能恢复，不保证**副作用不重复**——工具调用成功后、
checkpoint 落盘前进程崩溃，恢复会重新调用同一个工具（发邮件/建订单/转账都可能
被执行两次）。`EffectLedger`（`adapters/effects.py` 的 `SqliteEffectLedger`）是
第三个横切件，记录每次工具调用的 proposed→approved→executing→succeeded/failed
状态；恢复时先查账本再决定回放结果、安全重试、还是拒绝重试：

```python
from agent_kernel.adapters.effects import SqliteEffectLedger
from agent_kernel.types import RetryPolicy, ToolEffectPolicy, ToolSpec

effects = SqliteEffectLedger("runs/effects.db")
kernel = AgentKernel(model=model, tools=tools, planner=planner,
                      checkpoints=checkpoints, effects=effects)

# 工具自己声明是否幂等：
ToolSpec("send_email", "...", {}, effect_policy=ToolEffectPolicy(
    idempotent=False, retry_policy=RetryPolicy(max_attempts=1)))
```

- 已成功的工具调用：恢复时直接回放 `result_ref`，不重新执行。
- 停在 `executing`/`failed` 且工具幂等（未超重试上限）：安全自动重试。
- 停在 `executing`/`failed` 且工具非幂等：`resume()` 抛 `EffectUnresolvedError`，
  拒绝自动重试，需要人工核实外部系统真实状态、手动改账本后再恢复。
- `effects` 不传（默认 `None`）时行为跟改动前完全一致，未配置的 demo/eval 零影响。

不做的事：跨进程 2PC/外部系统自动核对、`idempotency_key` 自动注入工具参数、
指数退避——都在 [ADR-0008](docs/adr/0008-effect-ledger.md) 里写明为什么不做。

```powershell
.venv\Scripts\python.exe evals\run_effects.py --output evals/baseline-effects.json
```

三场景全部用真实崩溃模拟（`SystemExit` 逃出调用栈，两个独立 `AgentKernel` 实例
共享磁盘上的 checkpoint+账本文件）验证：非幂等工具停在 executing 被拒绝重试、
幂等工具安全自动重试、已成功但 checkpoint 未落盘时正确回放而非重复执行。见
[evals/baseline-effects.json](evals/baseline-effects.json)。

## 事件溯源：EventStore + reduce()

`EventBus` 之前只是旁路通知（观测/成本/审计各订各的，互不影响主循环）。
`adapters/event_store.py` 的 `SqliteEventStore` 把事件持久化成可查询的账本，
`event_sourcing.py` 的 `reduce()` 能从事件流重建出等价的 `RunState`——不重命名
任何现有事件，只在 9 个状态变更点新增同名新事件（`run.started`/`tool.proposed`/
`run.paused`/`tool.approved`/`tool.started`/`tool.completed`/`run.resumed`/
`run.completed`/`run.failed`），跟现有事件并存。

```python
from agent_kernel.adapters.event_store import SqliteEventStore
from agent_kernel.event_sourcing import reduce

store = SqliteEventStore("runs/events.db")
bus = EventBus()
bus.subscribe("*", store.handler())
kernel = AgentKernel(model=model, tools=tools, planner=planner, bus=bus, ...)

# 事故分析/审计/eval 重放：
events = store.load_events(run_id)
rebuilt_state = reduce(events)
```

- `SqliteEventStore` 是 `EventBus` 的普通订阅者，不是 port——继承
  `EventBus.publish` 现有的 `except Exception: pass`，**best-effort，不是
  exactly-once**；`resume()` 的权威数据源仍然是 `CheckpointStore`，不是事件
  账本，这是"加法"路线的核心边界（详见 [ADR-0009](docs/adr/0009-event-sourcing-and-fork.md)）。
- `reduce()` 重建不出 `context_summary`/`summarized_message_count`（`ContextBuilder`
  改的，不在内核状态变更点里）。
- `kernel.py` 新增的 9 处 `_emit` 在没有订阅者时就是几次哈希查找，零开销，
  不影响任何既有行为。

```powershell
.venv\Scripts\python.exe evals\run_event_sourcing.py --output evals/baseline-event-sourcing.json
```

3 个场景（批准/拒绝/步数耗尽）验证 `reduce(store.load_events(run_id))` 与真实
`checkpoints.load(run_id)` 逐字段相等。见
[evals/baseline-event-sourcing.json](evals/baseline-event-sourcing.json)。

## Fork：从历史 checkpoint 分支执行

`JsonCheckpointStore` 本来就给每个 `(turn, step)` 存一份快照文件
（`turn_NNN_step_NNN.json`），`fork()`（`src/agent_kernel/fork.py`）直接建在
这上面，**不依赖事件溯源**：读一份历史快照、深拷贝、换新 `run_id`，`kernel.py`
零改动——fork 出来的 `RunState` 交给现有 `AgentKernel(...).resume()` 就能继续跑。

```python
from agent_kernel.fork import fork

forked = fork(store, source_run_id="deploy-run", checkpoint="turn_1_step_1",
              new_run_id="deploy-approve")

# "批准 vs 拒绝"两条分支：同一个 fork 点，交给两个不同 approval 回调的内核
AgentKernel(model=model, tools=tools, planner=planner, checkpoints=store,
            approval=lambda call: True).resume(forked)
```

没有做用户最初设想的 `state_patch={"approved": False}` 参数——`RunState` 没有
`approved` 字段，通用任意字段 patch 有把状态改出内部不一致的风险；"批准 vs
拒绝"两条分支用两个不同 `approval` 回调各自 `resume()` 同一个 fork 点，零新增
接口面就能达到一样的效果。

```powershell
.venv\Scripts\python.exe examples\demo_fork.py
.venv\Scripts\python.exe evals\run_fork.py --output evals/baseline-fork.json
```

demo 用真实崩溃模拟（`approval` 回调 `raise SystemExit`）跑到"待审批、还没决定"
的 fork 点，然后并排跑批准/拒绝两条分支，打印真实分叉的结果（不是两套写死的
脚本——两条分支用同一份"回显最后一条真实消息"的模型代码，答案不同是因为状态
真的不同）。见 [evals/baseline-fork.json](evals/baseline-fork.json)。

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

`adapters/memory/composite.py` 的 `CompositeMemory(MemoryPort)` 把短期情景记忆（全量
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

### TTL + Importance 生命周期

`SqliteMemory` / `GraphMemory` 的 `add()` 支持可选 `importance: float = 1.0` 与
`ttl_seconds: float | None = None`：`search()` 按 `importance DESC, ts DESC` 排序，
且过滤掉已过期条目；`prune_expired()` 物理删除过期行，调用节奏由调用方决定（如离线
巩固脚本跑之前调一次），内核不主动调用。`PgVectorMemory` / `Neo4jGraphMemory` 暂未
对齐这两列（见各自源码里的 `ponytail:` 注释），没有可连的真实 Postgres/Neo4j 验证
schema 改动，真要用时照抄 `SqliteMemory`/`GraphMemory` 的列定义与过滤条件即可。

### Graph-Aware Consolidation：FOLLOWS / SIMILAR_TO / 用户画像

`evals/run_consolidation.py` 传入 `graph: GraphMemory` 参数后，巩固时额外把抽取到的
事实/偏好写入图谱，维护两类关系：

- `FOLLOWS`：同一 run 内本次抽取到的多条事实按抽取顺序两两相连，代表记忆演化链。
- `SIMILAR_TO`：新事实跟图谱里已有全部 `HAS_FACT` 事实做字符 2-gram Jaccard 相似度
  （中文无空格分词，退化用 2-gram 词袋；ponytail: 误判/漏判换成 embedding 余弦相似度，
  `PgVectorMemory` 已有 embedding，接进来即可），超过阈值 0.5 则连边。
- 每条事实/偏好同时挂一条 `HAS_FACT` / `HAS_PREFERENCE` 边到 `user:{run_id}` 节点——
  `build_profile(graph, run_id)` 读回这些边即用户画像（`run_id` 代理 user，见 ADR-0004
  无独立 user_id 概念的既定边界）。

```powershell
.venv\Scripts\python.exe evals\run_consolidation.py --mode offline
```

`--mode offline` 自检额外验证 `build_profile` 命中、`FOLLOWS`/`SIMILAR_TO` 边均已写入。

## Hybrid GraphRAG：向量 + BM25 关键词 + 图谱三路召回，RRF 融合

三路召回器都只是标准 `MemoryPort`：`MilvusMemory`（`adapters/memory/milvus.py`，向量腿，
真实 Milvus）、`ElasticsearchMemory`（`adapters/memory/elasticsearch.py`，关键词腿，
BM25 是 ES 默认 similarity）、`GraphMemory`/`Neo4jGraphMemory`（图腿，已有）。
`adapters/memory/hybrid_rag.py` 的 `HybridGraphRAG(MemoryPort)` 把三路组合起来，
`reciprocal_rank_fusion()`（标准 RRF，k=60）融合排序，图腿额外走 `multi_hop_search()`
BFS 多跳扩展。`add()` 只转发 vector+keyword 两路原始内容索引，graph 的实体/关系写入
是离线 ingestion（`evals/run_hybrid_rag.py`）的职责——跟 `CompositeMemory.add()` 只落
episodic 不重复写 semantic 同一个理由。

```python
hybrid = HybridGraphRAG(
    vector=MilvusMemory(uri, "kb"),
    keyword=ElasticsearchMemory(es_url, "kb"),
    graph=Neo4jGraphMemory(uri, user, password, "kb"),
    neighbors_fn=lambda node: graph.get_neighbors(node, "both", k=20),
    edges_fn=lambda: graph.search_edges(k=10000),
)
```

`evals/run_hybrid_rag.py`：`clean_markdown()`/`chunk_markdown()`（纯 stdlib 正则，标题
切块 + 段落滑窗 overlap）清洗切片，`ENTITY_EXTRACTION_PROMPT` 驱动 LLM 抽取实体/关系
写 `graph.add_edge()`。

```powershell
.venv\Scripts\python.exe evals\run_hybrid_rag.py --mode offline
```

`--mode offline`（`Bm25Memory` 真 BM25 算法 + `SqliteMemory` + `GraphMemory` 三路离线
拼装）两条核心断言：① RRF 融合排序正确——同时命中向量腿与关键词腿的内容排名高于
只命中一路的；② 多跳召回确实生效——查询词能通过两跳图扩展找到原文里完全没出现过
的关联事实（不是摆设）。

坦诚声明：`MilvusMemory`/`ElasticsearchMemory` 这次会话都**没能验证**。Milvus Lite
（`pymilvus[milvus_lite]`）官方只发布 Linux/macOS wheel，这台 Windows 机器上装得上
`pymilvus` 但嵌入模式连不上（已实测报 `ConnectionConfigException`）；Elasticsearch/
真实 Neo4j 需要 `docker compose up -d elasticsearch`，这次会话 docker daemon 没起。
跟 pgvector/neo4j 当初一样，按 ADR-0010 处理——离线 Fake（`Bm25Memory`/`GraphMemory`）
承担 CI 回归职责，`--mode real` 留给用户自己起真实服务后手动跑。详见
[ADR-0010](docs/adr/0010-hybrid-graphrag.md)。

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

策略对比（离线，5 策略 × 3 用例）：

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
| DAG | 3/3 | 1.00 | 拓扑并行 + Harness + Race，详见下节 |

### DAG 动态图 Runtime：并行调度 + Harness 容错 + Race Strategy

`planners/dag.py` 的 `DagPlanner(PlannerPort)`：模型把任务拆成带 `depends_on` 的节点图，
Kahn 拓扑排序按批执行——同批（入度为 0）的独立节点用 `ThreadPoolExecutor` 并行跑；每个
节点套一层 Harness：`timeout` 超时控制、`max_attempts` + 指数退避重试、`fallback` 备用
工具链；节点给 `candidates`（多个候选调用）时用 Race Strategy 并发竞速，`as_completed`
取最快返回的成功结果。全部 DAG 内部执行发生在一次 `planner.step()` 里，kernel 只看到
一次 `FinalAnswer`——`kernel.py`/`ports.py` 零改动，跟 `CompositeMemory` 一样是"新概念＝
新 adapter/策略"。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dag_planner.py -v
```

`tests/test_dag_planner.py` 6 项：独立节点确实并行（计时断言，串行需要 ~0.35s、并行
两批约 ~0.2s）、Race Strategy 取最快成功结果、Harness 重试退避后成功、fallback 链兜底、
循环依赖不挂死（拓扑排序检测不到的节点标记跳过，不进入死循环）、无需工具的简单查询
直接跳过整个 DAG（省一次模型调用）。

坦诚声明：DAG 内部工具调用不经过 EffectLedger/HITL approval、不经过 EventBus——这两个
横切面目前只在 kernel 的单步 `ToolCall` 路径生效，接入需要改 `PlannerPort` 契约或把
`EffectLedger`/`bus` 一并传给 planner，当前只读工具（搜索/检索类）场景不需要。超时基于
线程池、杀不掉线程，超时只是"不再等待"，不适合有副作用的重的工具。

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

## 可靠性与协议修复（2026-07-31）

一轮问题排查发现并修复了 4 处真实缺陷/缺口，全部有回归测试或 eval 覆盖：

- **Effect ID 跨轮冲突（严重）**：`effect_id` 之前是 `run_id:step`；多轮对话
  (`kernel.run()` 二次调用) 会把 `step` 清零重新计数，导致第二轮的 `effect_id`
  跟第一轮撞车——命中账本里第一轮的 `succeeded` 记录后，内核会直接把第一轮的
  结果回放给第二轮一个参数完全不同的工具调用。现改为 `run_id:turn:step`，并在
  回放/重试前校验账本里的 `arguments_hash` 是否跟本次调用一致，对不上直接抛
  `EffectArgumentMismatchError`（新错误类，`kernel.py`），拒绝用错的结果。
  见 `tests/test_effects.py::test_effect_id_scoped_by_turn_not_just_step` /
  `test_argument_hash_mismatch_raises`。
- **JSON 动作解析从"宽容降级"改成"重试一次再抛错"**：`ReactPlanner._parse()`
  之前解析失败会把模型的原始文本直接当成 `final` 答案静默返回给用户——模型
  输出格式错乱时用户会看到一段不知所云的答案，且没有任何信号表明出了问题。
  现在解析失败会用一条纠错提示追加喂回模型重试一次，仍失败则抛
  `ActionParseError`（`planners/react.py`），调用方能感知到失败，不会误把
  垃圾文本当答案。`plan_execute.py` 复用同一条路径。见 `tests/test_planners.py`。
- **原生 tool calling**：`ModelPort.complete()` 早就接收 `tools: list[ToolSpec]`
  参数，但 `LiteLLMModel` 一直没把它传给 `litellm.completion()`——模型只能靠
  提示词里的 JSON 格式说明"模拟"工具调用。现在有工具时会翻译成 OpenAI 风格的
  `tools=[...]` schema 传下去，响应里的 `tool_calls` 会被解析进新增的
  `ModelOutput.tool_calls` 字段，planner 优先采用（跳过文本 JSON 解析）。
  已在真实 DeepSeek 链路（`evals/run_worker_real.py`）验证走通；ponytail 边界：
  一步只取第一个 `tool_call`，暂不支持模型并行发起多个工具调用。见
  `tests/test_model_litellm.py`（monkeypatch，不打真实网络）。
- **`ToolPort.call()` 从只能返回 `str` 改成返回 `ToolResult`**：新增
  `ToolResult(content, artifacts)` 和 `ArtifactRef(uri, mime_type, description)`
  （`types.py`）。所有内置 toolbox（local/mcp/langchain/offload/skills/agents/
  sandbox）同步改造；`OffloadingToolbox` 落盘的长结果现在会正确挂一个
  `ArtifactRef` 而不是只在文本里拼路径字符串。effect ledger 与消息历史仍只落
  文本（`result.content` + artifact 行拼接），回放场景不恢复结构化 artifacts——
  ponytail 边界，见 `kernel.py::_finish_tool` 注释。见 `tests/test_tool_result.py`。

测试套件此前被移除（`9f3046b chore: remove test suite`），本轮在 `tests/` 下
用 pytest 重建，覆盖状态机（`run`/`resume` 合法与非法状态迁移、多轮 turn/step
语义）、effect ledger（跨轮冲突、参数哈希校验、三种崩溃恢复场景）、planner
解析重试、ToolResult：

```powershell
.tools\uv\Scripts\uv.exe sync  # 首次需要装 dev 依赖组（pytest）
.venv\Scripts\python.exe -m pytest tests/ -q
```

## 回归门禁

```powershell
.venv\Scripts\python.exe -m pytest tests/ -q
.venv\Scripts\python.exe evals\run_eval.py --mode offline
.venv\Scripts\python.exe evals\run_m3.py context
.venv\Scripts\python.exe evals\run_m5.py
.venv\Scripts\python.exe evals\run_composite_memory.py
.venv\Scripts\python.exe evals\run_preferences.py
.venv\Scripts\python.exe evals\run_consolidation.py --mode offline
.venv\Scripts\python.exe evals\run_effects.py
.venv\Scripts\python.exe evals\run_event_sourcing.py
.venv\Scripts\python.exe evals\run_fork.py
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
