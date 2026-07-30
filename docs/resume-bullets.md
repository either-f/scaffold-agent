# agent-kernel 简历要点

> 以下陈述均有代码、演示或基线数据作为直接证据。
> 所有数字来自 `evals/baseline-*.json`、离线评测或 `git ls-files` 计数。

## 项目简介

设计并实现了一个**微内核 + 六端口（Model/Tool/Memory/Planner/Skill/Interop）**的通用 Agent 框架（Python 3.11+），内核零第三方依赖，外部生态通过 adapter 模式接入。支持 ReAct / Plan-Execute / CodeAct / LangGraph 四种策略热替换，覆盖离线到真实 DeepSeek + MCP 全链路。

## 核心成果

| 指标 | 数值 | 证据 |
|------|------|------|
| 策略数 | 4 种可替换 | ReactPlanner / PlanExecutePlanner / CodeActPlanner / LangGraphPlanner |
| 离线 Eval | 30/30 | evals/tasks.jsonl + run_eval.py --mode offline |
| 真实 DeepSeek 基线 | 9/10 | evals/baseline-m2.json |
| 策略对比 | 12/12（4策略×3用例） | evals/baseline-m5.json |
| 多轮对话 | 50/50 轮 | evals/run_m3.py context，最大 prompt 23,342 字符 |
| 沙箱安全控制 | Docker --network none, --read-only, --cap-drop ALL, --security-opt no-new-privileges | src/agent_kernel/adapters/sandbox_docker.py |
| HITL | 逐工具 CLI 审批 + 断点恢复 | kernel.py + checkpoint.py |
| 观测 | OTel + Langfuse + JSONL 成本台账 | src/agent_kernel/adapters/observability.py |
| A2A 互操作 | Agent Card + HTTP task handler | src/agent_kernel/adapters/interop_a2a.py |
| Worker 委派 | 进程内 AgentKernel 委派 | src/agent_kernel/adapters/tools_agents.py |
| 图谱记忆 | 实体-关系-实体三元组存储 | src/agent_kernel/adapters/memory_graph.py |
| CI | GitHub Actions 离线门禁 | .github/workflows/ci.yml |

## 技术特色

1. **微内核架构**：六端口 + 两横切件（EventBus/Checkpoint）全部为抽象基类，任何新能力以 adapter/planner 形式接入而不改内核。
2. **渐进式技能系统**：采用 Anthropic Agent Skills 规范，元信息发现时不泄露正文，正文按需加载。新增技能 = 放一个 SKILL.md 文件夹，零代码变更。
3. **安全沙箱命令构建**：DockerSandbox 生成 --read-only --network none --cap-drop ALL --security-opt no-new-privileges 的锁定容器命令；离线模式下通过注入 runner 验证命令参数结构，不启动真实容器隔离证明。
4. **图状记忆**：在传统向量检索之外新增图记忆 adapter，支持实体-关系-实体三元组存储与邻居遍历，验证 MemoryPort 抽象在不改内核的前提下可扩展到图数据库范式。
5. **完整观测链**：EventBus 进程内发布/订阅，无需入侵内核即可接入 OpenTelemetry 导出器、Langfuse trace 记录器、JSONL 事件回放器、成本台账（当前无 self-host Langfuse 实时重放）。

## 可演示场景

三个离线确定性演示（见 `docs/demos/`）：
- **研究助手**：WorkerDelegationPort 委派研究 worker → 技能发现 → 正文加载 → 工具调用
- **文件审查**：动态技能文件夹发现 → 真实 pathlib 工具读取目录 → 只读审查建议
- **沙箱策略验证**：HITL 逐工具审批 → DockerSandbox 安全命令参数验证（离线干跑，不启动容器）

所有演示均可通过 `python examples/record_demos.py --check` 在无密钥/无网络/无 Docker daemon 环境下逐字节确定性验证。

## 坦诚声明

- Docker 命令/安全构建已通过离线演示验证；未提供真实容器隔离证明。
- OTel/Langfuse 导出器仅提供 adapter，无 self-host 实时重放证据。
- LangGraph 策略通过注入假图集成验证，未使用真实 StateGraph 编译流程。
- A2A 为标准库 HTTP server 实现的本地协议形适配器，非官方/完整 SDK 互操作。
- Worker 委派为真实进程内 AgentKernel 调用，但 demo 模型为确定性 FakeScriptedModel，非生产 LLM 基准。
