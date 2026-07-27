"""同步 ToolPort 到 MCP v1 stdio 客户端的最薄适配层。"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from concurrent.futures import Future
from typing import Any

from ..ports import ToolPort
from ..types import ToolSpec

Guard = Callable[[dict], None]


@dataclass
class StdioServerConfig:
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


class McpToolbox(ToolPort):
    """在一个后台 asyncio.Runner 中串行管理多个长生命周期 MCP 会话。"""

    def __init__(
        self,
        servers: Mapping[str, StdioServerConfig],
        allow: set[str] | None = None,
        guards: Mapping[str, Guard] | None = None,
    ) -> None:
        if not servers:
            raise ValueError("至少需要一个 MCP server")
        self.servers = dict(servers)
        self.allow = frozenset(allow or ())
        self.guards = dict(guards or {})
        self._requests: queue.Queue[tuple[str, Any, Future | None]] = queue.Queue()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._tools: list[ToolSpec] = []
        self._remote_names: dict[str, tuple[str, str]] = {}

    def __enter__(self) -> "McpToolbox":
        if self._thread is not None:
            raise RuntimeError("McpToolbox 已启动")
        self._thread = threading.Thread(target=self._thread_main, name="mcp-toolbox", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error:
            error = self._startup_error
            self._thread.join()
            self._thread = None
            raise RuntimeError(f"MCP 初始化失败: {error}") from error
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._thread is None:
            return
        self._requests.put(("close", None, None))
        self._thread.join()
        self._thread = None

    def list_tools(self) -> list[ToolSpec]:
        self._require_started()
        return list(self._tools)

    def call(self, name: str, args: dict) -> str:
        self._require_started()
        if name not in self.allow:
            raise PermissionError(f"工具未在白名单中: {name}")
        if name not in self._remote_names:
            raise KeyError(f"未知工具: {name}")
        if guard := self.guards.get(name):
            guard(args)
        future: Future[str] = Future()
        self._requests.put(("call", (name, args), future))
        return future.result()

    def _require_started(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("请在 with McpToolbox(...) 上下文中使用")

    def _thread_main(self) -> None:
        try:
            with asyncio.Runner() as runner:
                runner.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    async def _serve(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError("需要 MCP SDK：uv sync --extra mcp") from exc

        # ponytail: 单 worker 串行工具调用；有并发吞吐需求时再拆成每 server 一个 worker。
        async with AsyncExitStack() as stack:
            sessions: dict[str, Any] = {}
            all_tools: dict[str, ToolSpec] = {}
            for server_name, config in self.servers.items():
                params = StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env={**os.environ, **config.env},
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                sessions[server_name] = session
                response = await session.list_tools()
                for tool in response.tools:
                    qualified = f"{server_name}.{tool.name}"
                    if qualified in all_tools:
                        raise ValueError(f"重复 MCP 工具名: {qualified}")
                    all_tools[qualified] = ToolSpec(
                        name=qualified,
                        description=tool.description or "",
                        parameters=tool.inputSchema or {},
                    )
                    self._remote_names[qualified] = (server_name, tool.name)

            unknown = self.allow - all_tools.keys()
            if unknown:
                raise ValueError(f"白名单包含未知工具: {', '.join(sorted(unknown))}")
            self._tools = [all_tools[name] for name in sorted(self.allow)]
            self._ready.set()

            while True:
                operation, payload, future = self._requests.get()
                if operation == "close":
                    break
                try:
                    name, args = payload
                    server_name, remote_name = self._remote_names[name]
                    result = await sessions[server_name].call_tool(remote_name, arguments=args)
                    assert future is not None
                    future.set_result(self._result_text(result))
                except BaseException as exc:
                    assert future is not None
                    future.set_exception(exc)

    @staticmethod
    def _result_text(result: Any) -> str:
        if result.structuredContent is not None:
            text = json.dumps(result.structuredContent, ensure_ascii=False, default=str)
        else:
            from mcp import types

            parts: list[str] = []
            for content in result.content:
                if isinstance(content, types.TextContent):
                    parts.append(content.text)
                elif isinstance(content, types.EmbeddedResource) and isinstance(
                    content.resource, types.TextResourceContents
                ):
                    parts.append(content.resource.text)
                else:
                    raise TypeError(f"M1 不支持 MCP 内容类型: {type(content).__name__}")
            text = "\n".join(parts)
        if result.isError:
            raise RuntimeError(text or "MCP 工具执行失败")
        return text
