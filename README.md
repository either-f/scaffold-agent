# agent-kernel

微内核 + 插件端口的通用 agent 框架。内核零第三方依赖；外部生态只进入
`adapters/` 与 `planners/`。

## M0：离线运行

```powershell
$env:PYTHONPATH = "src"
python examples/run_demo.py
python tests/test_smoke.py
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

运行不需要密钥或网络的回归门禁：

```powershell
.venv\Scripts\python.exe tests\test_smoke.py
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

## M4：Skill 与沙箱

渐进式技能系统采用 Anthropic Agent Skills 规范（SKILL.md + 渐进披露）。技能工具
默认只披露 name+description，正文经 `load_skill` 工具按需加载。新增技能 =
往 `skills_library/` 放一个文件夹，零代码变更。

Docker 沙箱执行器通过 `--read-only --network none --cap-drop ALL --security-opt no-new-privileges`
锁定容器；`SandboxToolbox` 暴露唯一的 `python_execute` 工具供 CodeAct 策略使用。

```powershell
.venv\Scripts\python.exe tests\test_m4.py
```

## M5：观测与评测

事件总线（EventBus）发布/订阅模式支持外部观测器无侵入接入：
`ObservedModel` 发布 `model.complete` 事件、`CostLedger` 统计 token 费用、
`JsonlEventRecorder` 写事件回放日志、`OtelExporter` 与 `LangfuseExporter`
分别对接 OpenTelemetry 和 Langfuse。

策略对比（离线，4 策略 × 3 用例）：

```powershell
.venv\Scripts\python.exe evals\run_m5.py
.venv\Scripts\python.exe tests\test_m5.py
```

M5 策略离线基线：

| 策略 | 通过率 | 平均步数 | 备注 |
|------|:---:|:---:|------|
| ReAct | 3/3 | 2.00 | JSON 动作协议，无计划开销 |
| Plan-Execute | 3/3 | 2.00 | 首步额外生成计划 |
| CodeAct | 3/3 | 2.00 | 沙箱执行，安全隔离 |
| LangGraph | 3/3 | 2.33 | 图适配，含 HITL 形模拟 |

完整报告见 [evals/baseline-m5.json](evals/baseline-m5.json)。

## M6：多 Agent 与互操作

Worker 委派通过 `WorkerDelegationToolbox` 实现 orchestrator-worker 模式；
A2A 互操作通过 `A2AServer` 暴露 Agent Card 与 HTTP task handler；
图状记忆 adapter 以实体-关系-实体三元组验证 MemoryPort 抽象不需改内核即可扩展到图数据库范式。

```powershell
.venv\Scripts\python.exe tests\test_m6.py
```

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
.venv\Scripts\python.exe tests\test_smoke.py
.venv\Scripts\python.exe tests\test_m4.py
.venv\Scripts\python.exe tests\test_m5.py
.venv\Scripts\python.exe tests\test_m6.py
.venv\Scripts\python.exe evals\run_eval.py --mode offline
.venv\Scripts\python.exe evals\run_m3.py context
.venv\Scripts\python.exe evals\run_m5.py
.venv\Scripts\python.exe examples\record_demos.py --check
.venv\Scripts\python.exe -m compileall -q src examples evals tests
```

## 结果总览

| 里程碑 | 内容 | 状态 | 关键指标 |
|--------|------|:----:|------|
| M0 | 内核骨架 | ✅ | 纯标准库，离线可跑 |
| M1 | DeepSeek + MCP + LangChain | ✅ | 真实模型链路 |
| M2 | 恢复、审批与 Eval | ✅ | 离线 10/10，真模型 9/10 |
| M3 | 语义记忆 + 有界上下文 | ✅ | 语义检索 5/5，50 轮上下文 |
| M4 | Skill 与沙箱 | ✅ | 渐进披露 + Docker 隔离 |
| M5 | 观测与策略对比 | ✅ | 4 策略 12/12 |
| M6 | 多 Agent 与互操作 | ✅ | Worker + A2A + 图记忆 |
| M7 | 打包与求职物料 | ✅ | 3 个 demo + .cast + 简历 |

完整规划见 [PLAN.md](PLAN.md)。

## 扩展纪律

1. 第三方 import 只允许出现在 `adapters/` 与 `planners/`。
2. 每个真 adapter 必须有 Fake 对照，内核始终可离线运行。
3. 新依赖或借鉴点必须带 ADR 与真实运行证据。
