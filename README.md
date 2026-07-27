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
| 真实链路 | deepseek/deepseek-chat | 8/10 | 2.1 |

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
不做事实抽取、HNSW 或连接池。M3B 的多轮上下文压缩会在 M3A 合并后单独交付。

M3A 基线（2026-07-27）：语义查询 `5/5`，去重、tool 排除和 namespace 隔离均通过。
完整结果见 [evals/baseline-m3.json](evals/baseline-m3.json)。

完整规划见 [PLAN.md](PLAN.md)。

## 扩展纪律

1. 第三方 import 只允许出现在 `adapters/` 与 `planners/`。
2. 每个真 adapter 必须有 Fake 对照，内核始终可离线运行。
3. 新依赖或借鉴点必须带 ADR 与真实运行证据。
