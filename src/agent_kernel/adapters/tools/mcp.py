"""同步 ToolPort 到 MCP v1 stdio 客户端的最薄适配层。"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import threading
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from concurrent.futures import Future
from datetime import timedelta
from typing import Any

from ...ports import ToolPort
from ...types import ArtifactRef, ToolResult, ToolSpec

Guard = Callable[[dict], None]


@dataclass
class StdioServerConfig:
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


class McpToolbox(ToolPort):
    """每个 MCP server 一个后台 worker 线程，跨 server 并行、同 server 串行。"""

    def __init__(
        self,
        servers: Mapping[str, StdioServerConfig],
        allow: set[str] | None = None,
        guards: Mapping[str, Guard] | None = None,
        request_timeout_seconds: float = 30,
        retryable: set[str] | None = None,
        max_retries: int = 0,
        retry_delay_seconds: float = 1,
    ) -> None:
        if not servers:
            raise ValueError("至少需要一个 MCP server")
        self.servers = dict(servers)
        self.allow = frozenset(allow or ())
        self.guards = dict(guards or {})
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds 必须大于 0")
        if max_retries < 0 or retry_delay_seconds < 0:
            raise ValueError("重试次数和等待时间不能为负数")
        self.request_timeout_seconds = request_timeout_seconds
        self.retryable = frozenset(retryable or ())
        if unknown_retryable := self.retryable - self.allow:
            raise ValueError(f"retryable 包含未授权工具: {', '.join(sorted(unknown_retryable))}")
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

        # 每个 server 一个独立队列 + worker 线程，跨 server 不阻塞。
        self._queues: dict[str, queue.Queue[tuple[str, Any, Future | None]]] = {
            name: queue.Queue() for name in self.servers
        }
        self._threads: dict[str, threading.Thread] = {}
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._tools: list[ToolSpec] = []
        self._tools_index: dict[str, ToolSpec] = {}
        self._remote_names: dict[str, tuple[str, str]] = {}
        self._merge_lock = threading.Lock()
        self._init_done_count = 0

    def __enter__(self) -> "McpToolbox":
        if self._threads:
            raise RuntimeError("McpToolbox 已启动")
        for server_name in self.servers:
            thread = threading.Thread(
                target=self._thread_main,
                args=(server_name,),
                name=f"mcp-worker-{server_name}",
                daemon=True,
            )
            self._threads[server_name] = thread
            thread.start()
        self._ready.wait()
        if self._startup_error:
            error = self._startup_error
            # 先发 close 让还活着的 worker 退出，再 join，避免挂死。
            for q in self._queues.values():
                q.put(("close", None, None))
            self._join_all()
            self._threads = {}
            raise RuntimeError(f"MCP 初始化失败: {error}") from error
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if not self._threads:
            return
        for q in self._queues.values():
            q.put(("close", None, None))
        self._join_all()
        self._threads = {}

    def _join_all(self) -> None:
        for thread in self._threads.values():
            thread.join()

    def list_tools(self) -> list[ToolSpec]:
        self._require_started()
        return list(self._tools)

    def search_tools(self, query: str, k: int | None = None) -> list[ToolSpec]:
        """按名称/描述做大小写无关的子串检索；不足 k 项时用其余工具补齐。"""
        self._require_started()
        all_tools = list(self._tools)
        terms = [term for term in query.casefold().split() if term]
        matched = [
            tool
            for tool in all_tools
            if terms
            and any(
                term in f"{tool.name} {tool.description}".casefold()
                for term in terms
            )
        ]
        if k is None:
            return matched or all_tools
        if len(matched) < k:
            matched.extend(tool for tool in all_tools if tool not in matched)
        return matched[:k]

    def call(self, name: str, args: dict) -> ToolResult:
        self._require_started()
        if name not in self.allow:
            raise PermissionError(f"工具未在白名单中: {name}")
        if name not in self._remote_names:
            raise KeyError(f"未知工具: {name}")
        if guard := self.guards.get(name):
            guard(args)
        server_name, _ = self._remote_names[name]
        future: Future[ToolResult] = Future()
        self._queues[server_name].put(("call", (name, args), future))
        return future.result()

    def _require_started(self) -> None:
        if not self._threads or not any(t.is_alive() for t in self._threads.values()):
            raise RuntimeError("请在 with McpToolbox(...) 上下文中使用")

    def _thread_main(self, server_name: str) -> None:
        try:
            with asyncio.Runner() as runner:
                runner.run(self._serve(server_name))
        except BaseException as exc:
            # 只记录第一个 startup 错误，其余 worker 的错误在 join 后由主线程统一抛出。
            if self._startup_error is None:
                self._startup_error = exc
            self._ready.set()

    async def _serve(self, server_name: str) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError("需要 MCP SDK：uv sync --extra mcp") from exc

        config = self.servers[server_name]
        params = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env={**os.environ, **config.env},
        )
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(
                ClientSession(
                    read, write,
                    read_timeout_seconds=timedelta(seconds=self.request_timeout_seconds),
                )
            )
            await session.initialize()

            # 所有 server 完成各自工具发现后，主线程才放行 __enter__。
            # 用一个共享 dict 收集结果，最后一个完成的 worker 触发 _ready。
            response = await session.list_tools()
            local_tools: dict[str, ToolSpec] = {}
            local_remote_names: dict[str, tuple[str, str]] = {}
            for tool in response.tools:
                qualified = f"{server_name}.{tool.name}"
                local_tools[qualified] = ToolSpec(
                    name=qualified,
                    description=tool.description or "",
                    parameters=tool.inputSchema or {},
                )
                local_remote_names[qualified] = (server_name, tool.name)

            # 合并到共享状态，并在锁内推进初始化完成计数，避免竞态。
            with self._merge_lock:
                for qname in local_tools:
                    if qname in self._tools_index:
                        raise ValueError(f"重复 MCP 工具名: {qname}")
                self._tools_index.update(local_tools)
                self._remote_names.update(local_remote_names)
                self._init_done_count += 1
                is_last = self._init_done_count == len(self.servers)

            # 最后一个完成工具发现的 worker 负责校验白名单 & 放行 __enter__。
            if is_last:
                all_names = set(self._tools_index.keys())
                unknown = self.allow - all_names
                if unknown:
                    if self._startup_error is None:
                        self._startup_error = ValueError(
                            f"白名单包含未知工具: {', '.join(sorted(unknown))}"
                        )
                    self._ready.set()
                    return
                self._tools = [self._tools_index[name] for name in sorted(self.allow)]
                self._ready.set()

            while True:
                operation, payload, future = self._queues[server_name].get()
                if operation == "close":
                    break
                try:
                    name, args = payload
                    _, remote_name = self._remote_names[name]
                    for attempt in range(self.max_retries + 1):
                        try:
                            result = await session.call_tool(remote_name, arguments=args)
                            break
                        except Exception:
                            if name not in self.retryable or attempt >= self.max_retries:
                                raise
                            await asyncio.sleep(self.retry_delay_seconds)
                    assert future is not None
                    future.set_result(self._map_result(result))
                except BaseException as exc:
                    assert future is not None
                    future.set_exception(exc)

    # ----------------------------------------------------------------- result mapping

    @staticmethod
    def _map_result(result: Any) -> ToolResult:
        """把 MCP ``CallToolResult`` 映射成 ``ToolResult``。

        - ``structuredContent`` 非空：JSON 预览进 ``content``，结构化数据以 ``data:`` URI
          进 ``artifacts``（最小正确实现：无需落盘，in-memory 合成引用）。
        - ``EmbeddedResource`` 携带 URI：该 URI 直接作为 ``ArtifactRef.uri``。
        - 无法识别的内容类型：安全降级为占位文本，不中断整个工具调用。
        """
        from mcp import types

        text_parts: list[str] = []
        artifacts: list[ArtifactRef] = []

        if result.structuredContent is not None:
            text_parts.append(json.dumps(result.structuredContent, ensure_ascii=False, default=str))
            # 结构化数据无外部 URI，合成 data: URI 作为最小正确引用。
            raw = json.dumps(result.structuredContent, ensure_ascii=False, default=str).encode("utf-8")
            data_uri = "data:application/json;base64," + base64.b64encode(raw).decode("ascii")
            artifacts.append(
                ArtifactRef(
                    uri=data_uri,
                    mime_type="application/json",
                    description="MCP structuredContent",
                )
            )

        for content in result.content:
            if isinstance(content, types.TextContent):
                text_parts.append(content.text)
            elif isinstance(content, types.EmbeddedResource):
                resource = content.resource
                if isinstance(resource, types.TextResourceContents):
                    text_parts.append(resource.text)
                    uri = str(resource.uri) if resource.uri else None
                elif isinstance(resource, types.BlobResourceContents):
                    uri = str(resource.uri) if resource.uri else None
                else:
                    uri = None
                if uri:
                    mime = getattr(resource, "mimeType", None) or "application/octet-stream"
                    artifacts.append(
                        ArtifactRef(uri=uri, mime_type=mime, description="MCP embedded resource")
                    )
            else:
                # 安全降级：不识别的内容类型用占位符标注，不中断整个工具调用。
                text_parts.append(f"[unsupported MCP content type: {type(content).__name__}]")

        text = "\n".join(text_parts)
        return ToolResult(
            content=text or ("MCP 工具执行失败" if result.isError else ""),
            artifacts=artifacts,
            is_error=bool(result.isError),
            structured_content=result.structuredContent,
        )
