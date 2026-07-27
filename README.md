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

完整规划见 [PLAN.md](PLAN.md)。

## 扩展纪律

1. 第三方 import 只允许出现在 `adapters/` 与 `planners/`。
2. 每个真 adapter 必须有 Fake 对照，内核始终可离线运行。
3. 新依赖或借鉴点必须带 ADR 与真实运行证据。
