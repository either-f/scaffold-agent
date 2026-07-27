# ADR-0002: M1 运行时适配边界

日期: 2026-07-27  状态: 已采纳

## 决策

- LiteLLM 只实现 `ModelPort`，继续使用原生 ReAct JSON 动作协议。
- MCP Python SDK 固定 `>=1.28,<2`；同步 `ToolPort` 由后台 `asyncio.Runner`
  串行桥接长生命周期 stdio 会话，工具名加 server 前缀并默认拒绝。
- LangChain Core 只包装 `BaseChatModel` 与 `BaseTool`，不接管 Agent 循环。

## 原因

MCP v2 尚在迁移期；直接把 Kernel 改成异步会扩大 M1 改动。当前桥接保留端口契约，
同时让 Filesystem 与 Fetch 共用一个可正确关闭的生命周期。

## 后果与迁移条件

- M1 工具调用串行；只有并发工具吞吐成为瓶颈时才拆成每 server worker。
- M2 再加入超时、重试和恢复；M5 出现真实分支/循环工作流时再实现 LangGraph Planner。
- MCP v2 稳定并有迁移收益后，通过单独 ADR 升级，不在依赖范围内自动跨大版本。
