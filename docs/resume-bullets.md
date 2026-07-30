# agent-kernel 简历要点

> 以下陈述均有代码/测试/基线数据作为直接证据。
> 所有数字来自 `evals/baseline-*.json` 与 `tests/test_*.py` 实测记录。

## 项目简介

设计并实现了一个**微内核 + 六端口（Model/Tool/Memory/Planner/Skill/Interop）**的通用 Agent 框架（Python 3.11+），内核零第三方依赖，外部生态通过 adapter 模式接入。支持 ReAct / Plan-Execute / CodeAct / LangGraph 四种策略热替换，覆盖离线到真实 DeepSeek + MCP 全链路。

## 核心成果

| 指标 | 数值 | 证据 |
|------|------|------|
| 内核代码行数 | ~1,200（不含 adapter） | src/agent_kernel/kernel.py + types.py + ports.py + events.py + checkpoint.py |
| 策略数 | 4 种可替换 | ReactPlanner / PlanExecutePlanner / CodeActPlanner / LangGraphPlanner |
| Adapter 数 | 15 个 | 含 model_fake, model_litellm, model_langchain, tools_local, tools_mcp, tools_skills, tools_offload, tools_agents, sandbox_docker, memory_sqlite, memory_pgvector, memory_graph, observability (OTel+Langfuse), interop_a2a |
| 离线 Eval | 30/30 | evals/tasks.jsonl + run_eval.py --mode offline |
| 真实 DeepSeek 基线 | 9/10 | evals/baseline-m2.json |
| 策略对比 | 12/12（4策略×3用例） | evals/baseline-m5.json |
| 测试用例 | 66+ | tests/test_smoke.py + test_m4.py + test_m5.py + test_m6.py |
| 多轮对话 | 50/50 轮 | evals/run_m3.py context，最大 prompt 23,342 字符 |
| 沙箱 | Docker 隔离：--network none, --read-only, --cap-drop ALL, --security-opt no-new-privileges | src/agent_kernel/adapters/sandbox_docker.py |
| HITL | 逐工具 CLI 审批 + 断点恢复 | kernel.py + checkpoint.py + tests/test_smoke.py test_hitl_veto |
| 观测 | OTel + Langfuse + JSONL 成本台账 | src/agent_kernel/adapters/observability.py |
| A2A 互操作 | Agent Card + HTTP task handler | src/agent_kernel/adapters/interop_a2a.py |
| CI | GitHub Actions 离线门禁（冒烟+eval+策略+编译检查） | .github/workflows/ci.yml |

## 技术特色

1. **微内核架构**：内核约 1,200 行纯标准库代码，六端口 + 两横切件（EventBus/Checkpoint）全部为抽象基类，任何新能力以 adapter/planner 形式接入而不改内核。
2. **渐进式技能系统**：采用 Anthropic Agent Skills 规范，元信息发现时不泄露正文，正文按需加载。新增技能 = 放一个 SKILL.md 文件夹，零代码变更。
3. **安全沙箱**：Docker 容器 --read-only 根文件系统，--network none 零网络，--cap-drop ALL 禁止提权，--tmpfs /tmp:noexec 唯一可写处。CodeAct 策略让模型在隔离容器中写代码执行。
4. **图状记忆**：在传统向量检索之外新增图记忆 adapter，支持实体-关系-实体三元组存储与邻居遍历，验证 MemoryPort 抽象在不改内核的前提下可扩展到图数据库范式。
5. **完整观测链**：EventBus 进程内发布/订阅，无需入侵内核即可接入 OpenTelemetry 导出器、Langfuse trace 记录器、JSONL 事件回放器、成本台账。

## 可演示场景

三个完整离线演示（见 `docs/demos/`）：
- **研究助手**：技能发现→正文加载→多步工具调用→结论输出
- **文件整理**：动态技能文件夹发现→工具委托→安全文件操作
- **沙箱运维**：HITL 逐工具审批→Docker 隔离 CodeAct 执行

所有演示均可通过 `python examples/record_demos.py --check` 在无密钥/无网络环境下验证通过。
