# 通用 Agent 平台开发计划（agent-kernel）

> 定位：一个**微内核 + 插件端口**的通用 agent 框架。所有外部开源项目只允许进入 adapter 层，
> 新概念（新协议 / 新记忆方案 / 新编排范式）出现时，接入方式永远是"加插件"，不是"改内核"。
> 业务逻辑（管理系统、桌面归纳等）后续以 MCP server / Skill 的形式挂载，与内核解耦。

---

## 0. 目标与非目标

**目标**
1. 高扩展性：六个端口（Model / Tool / Memory / Planner / Skill / Interop）+ 两个横切件（事件总线、Checkpoint），任何变化点都被隔离在接口后面。
2. 生产化三件套：eval 回归、tracing + 成本统计、沙箱执行 —— 这是面试区分度所在。
3. 融合而非重造：能当依赖直接用的（LiteLLM、MCP SDK、Langfuse）就直接用；只有内核和端口定义自己写。

**非目标（防烂尾红线）**
- 不自研模型、不训模型。
- M5 之前不做多 agent（行业共识：先单 agent + MCP，确有需要再上多 agent）。
- 不做大而全 Web UI，CLI + 简单 trace 页面即可。
- 不追求兼容所有框架，只保证"任何框架的思想都能以策略/适配器形式接入"。

---

## 1. 总体架构

```
                        ┌─────────────────────────────┐
   用户/CLI/API ──────▶ │        Agent Kernel          │
                        │  组装上下文→调模型→分发动作→更新状态 │
                        └──┬────┬────┬────┬────┬────┬──┘
                           │    │    │    │    │    │
                        Model Tool Memory Plan Skill Interop   ← 六个端口(ABC)
                           │    │    │    │    │    │
                        LiteLLM MCP  sqlite ReAct SKILL.md A2A ← 可替换 adapter
                              (FastMCP) /pgvector /P-E/CodeAct
        横切件：EventBus（观测/评测/HITL/审计都订阅事件，不侵入内核）
                CheckpointStore（每步可恢复、可回放）
```

**技术基线**：Python 3.11+（内核纯标准库，adapter 才引依赖）、uv 管环境；
Java 17 + Spring Boot 3 + Spring AI（业务插件侧，暴露 MCP server）；PostgreSQL + pgvector（M3 起）。

---

## 2. 开源融合清单（借什么、怎么融）

| 层 | 直接采用（当依赖） | 借鉴设计（读源码抄思想，不引包） | 融合方式 |
|---|---|---|---|
| 模型路由 | **LiteLLM**（BerriAI/litellm，统一 100+ 模型 API） | OpenRouter 的降级/路由策略 | 包在 `ModelPort` adapter 里，内核只见 `complete()` |
| 工具层 | **MCP 官方 Python SDK**（modelcontextprotocol/python-sdk，含 FastMCP 写 server） | lastmile-ai/mcp-agent 的多 server 生命周期管理 | `ToolPort` 的 MCP adapter；本地函数工具与 MCP 工具同接口 |
| 编排内核 | 自研（几百行） | **LangGraph** 的 checkpoint/interrupt 语义；**OpenAI Agents SDK** 的 handoff+guardrail；**Claude Agent SDK** 的 hooks/子 agent/"给 agent 一台电脑"；**smolagents** 的 CodeAct（agent 写代码调工具） | checkpoint/HITL 进内核横切件；handoff、CodeAct 各做成一个 `PlannerPort` 策略 |
| LangChain 生态兼容 | **LangChain Core**（可选依赖） | `BaseChatModel` / `BaseTool` 的组件协议 | 分别包装成 `ModelPort` / `ToolPort` adapter；只复用现成生态组件，不让 Chain/Agent 运行时进入内核 |
| 复杂工作流 | **LangGraph**（可选依赖） | 状态图、分支/循环、持久化中断 | 实现 `LangGraphPlanner(PlannerPort)`；简单 ReAct 仍走原生策略，确有复杂状态图时才启用 |
| 记忆 | 起步自研 sqlite/pgvector | **Mem0**（mem0ai/mem0）的 add/search/update API 形态；**Letta**（letta-ai/letta，MemGPT 系）core/archival 分层；Zep/Graphiti 的时序知识图谱 | `MemoryPort` 定成 Mem0 风格接口；图谱记忆 = M6 的一个新 adapter，用来验证扩展性 |
| Skill 能力包 | 采用 **Anthropic Agent Skills 规范**（SKILL.md + 渐进披露，参考 anthropics/skills） | 社区 Agent-Skills-for-Context-Engineering 仓库的技能组织方式 | `SkillLoader` 只把 name+description 注入上下文，正文按需加载 |
| 沙箱 | 本地 **Docker** 起步 | **E2B** 的 sandbox API 形态（对齐接口，之后可切云沙箱）；Open Interpreter 的执行审计 | `SandboxExecutor` 接口，M4 落地 |
| 观测 | **OpenTelemetry**（GenAI 语义约定）+ **Langfuse** self-host | LangSmith 的 trace 视图组织 | 一个订阅 EventBus 的 exporter，内核零感知 |
| Eval | **promptfoo** 或 **DeepEval** 二选一 | OpenAI evals 的任务集组织；HAL 式"同模型换 scaffold 对比" | `evals/tasks.jsonl` + 每个 PR 跑回归；策略 A/B 出分数写进 README |
| Interop | **a2a-python SDK**（A2A 协议，Linux 基金会 AAIF 治理） | Agent Card 的能力发现设计 | `InteropPort` adapter，M6；让本 agent 可被别的 agent 网络发现 |
| 整体参考 | — | **OpenHands**（All-Hands-AI）的事件流架构；**OpenManus / OWL**（camel-ai）的通用任务分解；**gpt-researcher** 的研究工作流；**browser-use** 的浏览器工具设计 | 只抄任务分解与工具颗粒度，不引它们的运行时 |

**融合纪律（"一定要融合好"的具体执行）**
1. 外部包的 import 只允许出现在 `src/agent_kernel/adapters/` 与 `planners/` 下，内核目录禁止第三方 import（CI 加检查）。
2. 每引入一个借鉴点，写一条 ADR（`docs/adr/`）：借了什么、为什么、换掉它要动哪几行。
3. 每个 adapter 必须有一个"假实现"（Fake）同接口对照，保证内核可离线测试。
4. LangChain / LangGraph 仅设计为可通过 optional extra 接入；当前未配置真实的 LangGraph 依赖或 StateGraph 编译流程。

---

## 3. 端口接口规格（与代码一致）

```python
class ModelPort:      complete(messages, tools) -> ModelOutput           # 换模型=换 adapter
class ToolPort:       list_tools() -> [ToolSpec]; call(name, args) -> str # 本地函数与 MCP 同接口
class MemoryPort:     add(run_id, role, content); search(query, k) -> [str]
class PlannerPort:    step(state, model, tools, memory) -> ToolCall | FinalAnswer  # 策略可替换
class SkillLoader:    list_skills() -> [SkillMeta]; load(name) -> str    # 渐进披露
class CheckpointStore: save(state); load(run_id) -> RunState             # 每步落盘，可恢复
class EventBus:       publish(event); subscribe(type, handler)           # 观测/HITL/审计入口
class InteropPort:    (M6) agent_card(); handle_task(task)            # A2A
```

HITL（人工介入）挂在内核的 `approval` 回调：工具执行前发 `tool.before` 事件并询问回调，
拒绝则把否决写回上下文——这与 LangGraph `interrupt()` 语义等价，但实现只有十几行。

---

## 4. 目录结构

```
agent-kernel/
├── PLAN.md                     # 本文件
├── README.md                   # 快速上手
├── pyproject.toml              # 内核零依赖；extras: model/mcp/obs/eval
├── src/agent_kernel/
│   ├── types.py                # Message/ToolSpec/ToolCall/FinalAnswer/Event/RunState
│   ├── ports.py                # 六端口 + 两横切件的抽象基类
│   ├── kernel.py               # 主循环（组装→调模型→分发→更新→checkpoint）
│   ├── events.py               # 进程内事件总线
│   ├── checkpoint.py           # JSON checkpoint（M3 换 sqlite/postgres adapter）
│   ├── planners/react.py       # ReAct 策略（JSON 动作协议）
│   ├── planners/langgraph.py   # 可选 LangGraph 复杂工作流策略（M5）
│   ├── skills/loader.py        # SKILL.md 解析与渐进披露
│   └── adapters/
│       ├── model_fake.py       # 脚本化假模型（离线测试用）
│       ├── model_litellm.py    # LiteLLM 真模型（extras: model）
│       ├── model_langchain.py  # 可选 BaseChatModel → ModelPort 兼容层（M1）
│       ├── tools_local.py      # 本地函数工具箱（含 now/calc 演示工具）
│       ├── tools_langchain.py  # 可选 BaseTool → ToolPort 兼容层（M1）
│       ├── tools_mcp.py        # MCP 客户端 adapter（M1 落地，含骨架）
│       └── memory_sqlite.py    # sqlite 记忆（M3 换 pgvector）
├── skills_library/web-research/SKILL.md   # 示例技能包
├── examples/run_demo.py        # 可直接运行的端到端 demo（离线）
└── docs/adr/0001-ports-architecture.md
```

---

## 5. 里程碑

### M0 内核骨架 ✅（本次已交付）
- 产出：上表全部文件；纯标准库、离线可跑；demo 演示"两次工具调用→最终答案"，全程事件可见、每步 checkpoint。
- 验收：`python examples/run_demo.py` 输出正确答案。

### M1 接真实世界 ✅
- LiteLLM adapter 联调 1–2 家模型；MCP adapter 完成（stdio 起服务、list_tools、call）；接入 2 个现成 MCP server（filesystem、fetch）；主链跑通后补最薄的 LangChain `BaseChatModel` / `BaseTool` 兼容 adapter。
- 验收：真模型 + MCP 工具跑通同一 demo；工具白名单配置生效；任选一个 LangChain 模型或工具无需修改内核即可替换接入。

### M2 可靠性 ✅
- checkpoint 恢复（`--resume run_id`）；HITL 审批走通（CLI y/n）；超时与重试；最小 eval 集（10 个任务）+ 跑分脚本。
- 验收：kill 掉进程后能从断点恢复；eval 出基线分数并写入 README。
- 实测：真实进程在第二个工具审批处中断后从 pending tool 恢复；离线 eval 10/10，DeepSeek + 只读 MCP 9/10。

### M3 记忆与上下文工程 ✅
- Postgres + pgvector adapter（Mem0 风格接口）；上下文组装管线：offload（长内容落盘引用）、reduce（历史压缩摘要）、按需检索。
- 验收：50 轮长对话不爆上下文；关键信息跨会话可检索；eval 分数不降。
- M3A 已完成：pgvector 精确余弦检索、DashScope 1024 维 embedding、namespace 隔离；真实语义检索 5/5。
- M3B 已完成：同 run_id 多轮续聊、turn/step checkpoint、长工具结果外置与增量历史摘要。
- 实测：50 轮离线对话最大 ReAct prompt 23,342 字符；DeepSeek 跨会话正确回忆 `Mercury` 与中文偏好。

### 扩展：偏好与约束记忆（2026-07-31）✅
- `ReactPlanner` 新增可选 `preferences: MemoryPort`，固定 query 检索、每轮无条件注入 system prompt，
  跟按当前输入相关性检索的常规记忆是两条独立路径。只做读取；自动提取偏好陈述写入
  `namespace="preferences"` 留给后续的离线记忆巩固脚本。
- 验收：`evals/run_preferences.py`——偏好块不依赖当前 query 相关性注入；未配置时不产生多余内容；已入 CI。

### M4 Skill 与沙箱（3–4 天）✅（离线验收）
- SkillLoader 接入内核（渐进披露）；Docker 沙箱执行器（CodeAct 策略在沙箱里跑代码）。
- 验收：新增一个技能 = 只放一个文件夹；沙箱内代码无法访问宿主敏感路径。
- 已完成：SkillToolbox 渐进式技能披露（list_tools 只漏 name+desc，正文经 load_skill 按需加载）；DockerSandbox --read-only --network none --cap-drop ALL 安全锁定；CodeActPlanner 端到端通。
- 坦诚声明：Docker 命令/安全构建经离线 demo 注入 runner 验证参数，未启动真实容器隔离证明。

### M5 观测与评测完备（4–6 天）✅（离线验收）
- OTel + Langfuse self-host；token/成本统计订阅事件落库；eval 扩到 30+ 任务，ReAct vs Plan-Execute vs CodeAct 三策略对比出报告；用一个确有分支/循环/HITL 的任务验证 `LangGraphPlanner`。
- 验收：任一 run 可在 Langfuse 里逐步回放；README 有策略对比表，并给出原生 Planner 与 LangGraph 实现的复杂度、恢复能力和效果对比。
- 已完成：ObservedModel + JsonlEventRecorder + CostLedger + OtelExporter + LangfuseExporter 全部通过 EventBus 订阅实现；4 策略 × 3 用例离线对比 12/12；LangGraphPlanner 已验证 final/tool/HITL 三条分支。
- M5 策略基线报告见 evals/baseline-m5.json。
- 坦诚声明：LangGraphPlanner 当前使用注入的假图（fake graph）做集成验证，未打包真实 LangGraph StateGraph 编译流程——当前未配置 LangGraph 依赖或 optional extra，无真实的 StateGraph 集成。OTel/Langfuse 导出器仅提供 adapter，无 self-host Langfuse 实时重放证据。

### M6 多 agent 与互操作（5–8 天）✅（本地验收/协议骨架）
- 子 agent（orchestrator-worker，借 Claude Agent SDK 分层派生思想）；A2A adapter + Agent Card；图谱记忆 adapter（验证 MemoryPort 扩展性）。
- 验收：一个新概念（图谱记忆）的接入 PR 不改内核任何文件。
- 已完成：WorkerDelegationPort orchestrator-worker 模式；A2AInteropAdapter + create_a2a_server 提供 Agent Card 与 HTTP task handler；GraphMemory 实体-关系-实体图状记忆 adapter。图谱记忆接入全在 `adapters/memory_graph.py`，内核零改动——验证 MemoryPort 扩展性。
- 生产推进（2026-07-30）：新增 `Neo4jGraphMemory`（`adapters/memory_graph_neo4j.py`），真实 Neo4j + Cypher 存查三元组，取代 SQLite 版本作为生产 adapter；`GraphMemory` 降级为离线 Fake。详见 [ADR-0006](docs/adr/0006-graph-memory-neo4j.md) 与 `evals/run_graph.py`。
- 坦诚声明：A2A interop 使用 Python 标准库 `http.server` + `threading` 实现轻量 HTTP server，A2AInteropAdapter + create_a2a_server 提供 Agent Card 与 task handler，未引入 a2a-python SDK——Agent Card 为协议形态的本地格式，非 A2A 官方兼容，但完整协议握手（task status 推送、streaming、认证）仅做骨架。Worker 委派当前 "mini-kernel" 由 FakeScriptedModel 驱动，实际生产应换用 LiteLLMModel。

### M7 打包与求职物料（2–3 天）✅
- README 讲清架构故事；3 个 demo 场景录屏（研究助手 / 文件整理 / 带审批的运维操作）；简历 bullet 初稿。
- 已完成：README 涵盖 M0-M7 全部使用说明与结果表；3 个离线确定性 demo（research/files/ops）均使用 FakeScriptedModel；asciicast v2 .cast 录制器纯标准库实现、确定性输出；简历要点见 docs/resume-bullets.md。
- 坦诚声明：research demo 使用真实 WorkerDelegationPort 进程内委派；files demo 使用真实 pathlib 工具读取文件；ops demo 验证 Docker 命令安全参数但不启动容器。录制为确定性 asciicast（固定尺寸、单调偏移、无 timestamp）。
- 交付物：examples/demo_{research,files,ops}.py + examples/record_demos.py + docs/demos/*.cast + docs/resume-bullets.md。CI 扩展至 M4/M5/M6 全量测试 + demo --check + 内核 AST 第三方 import 检查。

### 并行 Java 线（穿插进行）
- J1：Spring AI 把"通知中枢"暴露成 MCP server → 成为本 agent 第一个业务插件（M1 后即可做）。
- J2：管理系统 CRUD 以 MCP 工具形式挂载（M4 后）。

---

## 6. 工程规范
- 测试：内核逻辑全部用 Fake adapter 离线测；adapter 单独集成测。
- CI：lint + 内核目录第三方 import 检查 + 冒烟 + eval 回归（分数下降即红）。
- 提交：conventional commits；prompt 全部进版本库（`prompts/` 目录），改 prompt 必附 eval 对比。
- 安全基线：密钥只走环境变量；工具默认白名单制；沙箱默认开启；MCP server 来源审查。

## 7. 新概念接入手册（回答"以后出新东西怎么办"）
1. 判定它属于哪个端口（新模型→Model；新工具/协议→Tool 或 Interop；新记忆→Memory；新编排范式→Planner；新能力→Skill）。
2. 在 `adapters/` 或 `planners/` 写实现，同步写一个 Fake。
3. 加 3–5 个 eval 任务证明有效，跑回归确认无退化。
4. 写 ADR。全程内核零改动——如果发现必须改内核，先讨论是不是端口设计漏了，补端口而不是打补丁。

## 8. 风险与止损
- 范围蔓延 → 每个里程碑只允许引入一个新概念；两周没有可运行增量就砍范围。
- 框架绑定 → 融合纪律第 1 条由 CI 强制。
- eval 缺位 → M2 起任何合并必须带回归结果。
- 时间：业余节奏总计约 1.5–2 个月；M0–M2 即可写进简历，后续为增量。
