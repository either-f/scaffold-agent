"""MCP toolbox 测试：结构化内容映射、未知类型降级、跨 server 并发。

运行：.venv\\Scripts\\python.exe -m pytest tests/test_mcp.py -q
"""
import asyncio
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")

from agent_kernel.adapters.tools.mcp import McpToolbox, StdioServerConfig
from agent_kernel.types import ArtifactRef, ToolResult


# --------------------------------------------------------------------------- #
# _map_result 单元测试：不依赖真实 MCP server，直接构造 CallToolResult
# --------------------------------------------------------------------------- #

def _make_mcp_result(content=None, structured=None, is_error=False):
    """构造一个最小化的 MCP CallToolResult-like 对象。"""
    from mcp import types

    return types.CallToolResult(
        content=content or [],
        structuredContent=structured,
        isError=is_error,
    )


def test_structured_content_produces_artifact():
    """structuredContent 非空时，ToolResult 必须附带至少一个 ArtifactRef。"""
    result = _make_mcp_result(structured={"key": "value", "count": 42})
    mapped = McpToolbox._map_result(result)

    assert isinstance(mapped, ToolResult)
    assert len(mapped.artifacts) >= 1
    art = mapped.artifacts[0]
    assert art.uri.startswith("data:application/json;base64,")
    assert art.mime_type == "application/json"
    assert "structuredContent" in art.description
    assert '"key": "value"' in mapped.content
    assert '"count": 42' in mapped.content


def test_structured_content_data_uri_roundtrip():
    """data: URI 编码的 structuredContent 能解码回原始 JSON。"""
    import base64
    import json

    payload = {"x": [1, 2, {"y": "z"}]}
    result = _make_mcp_result(structured=payload)
    mapped = McpToolbox._map_result(result)

    art = mapped.artifacts[0]
    # 去掉 data: 前缀，base64 解码
    encoded = art.uri.split("base64,", 1)[1]
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded == payload


def test_embedded_resource_text_produces_artifact_with_uri():
    """EmbeddedResource 携带 TextResourceContents 时，resource.uri 作为 ArtifactRef.uri。"""
    from mcp import types

    resource = types.TextResourceContents(
        uri="file:///tmp/data.json",
        mimeType="application/json",
        text='{"embedded": true}',
    )
    content = types.EmbeddedResource(type="resource", resource=resource)
    result = _make_mcp_result(content=[content])

    mapped = McpToolbox._map_result(result)
    assert '{"embedded": true}' in mapped.content
    assert any(a.uri == "file:///tmp/data.json" for a in mapped.artifacts)
    art = next(a for a in mapped.artifacts if a.uri == "file:///tmp/data.json")
    assert art.mime_type == "application/json"


def test_embedded_resource_blob_produces_artifact_with_uri():
    """BlobResourceContents 的 URI 也应作为 ArtifactRef。"""
    import base64 as b64
    from mcp import types

    resource = types.BlobResourceContents(
        uri="file:///tmp/image.png",
        mimeType="image/png",
        blob=b64.b64encode(b"\x89PNG").decode("ascii"),
    )
    content = types.EmbeddedResource(type="resource", resource=resource)
    result = _make_mcp_result(content=[content])

    mapped = McpToolbox._map_result(result)
    assert any(a.uri == "file:///tmp/image.png" for a in mapped.artifacts)
    art = next(a for a in mapped.artifacts if a.uri == "file:///tmp/image.png")
    assert art.mime_type == "image/png"


def test_unsupported_content_type_degrades_gracefully():
    """无法识别的 MCP 内容类型应降级为占位文本，不抛异常。"""
    from mcp import types

    # ImageContent 是当前 _map_result 不显式处理的类型
    image = types.ImageContent(type="image", data="base64data", mimeType="image/png")
    text = types.TextContent(type="text", text="hello")
    result = _make_mcp_result(content=[text, image])

    mapped = McpToolbox._map_result(result)
    assert "hello" in mapped.content
    assert "[unsupported MCP content type: ImageContent]" in mapped.content
    assert mapped.artifacts == []


def test_error_result_still_raises():
    """isError=True 时仍应抛 RuntimeError（保持原有行为）。"""
    result = _make_mcp_result(
        content=[_make_text_content("boom")],
        is_error=True,
    )
    with pytest.raises(RuntimeError, match="boom"):
        McpToolbox._map_result(result)


def test_plain_text_content_no_artifacts():
    """纯文本 content（无 structuredContent、无 resource）不产生 artifacts。"""
    from mcp import types

    result = _make_mcp_result(content=[
        types.TextContent(type="text", text="line1"),
        types.TextContent(type="text", text="line2"),
    ])
    mapped = McpToolbox._map_result(result)
    assert mapped.content == "line1\nline2"
    assert mapped.artifacts == []


def _make_text_content(text: str):
    from mcp import types
    return types.TextContent(type="text", text=text)


# --------------------------------------------------------------------------- #
# 跨 server 并发测试：注入 fake session，验证慢 server 不阻塞快 server
# --------------------------------------------------------------------------- #

class _FakeSession:
    """不依赖 stdio 的假 MCP session，可注入延迟。"""

    def __init__(self, tool_name: str, delay: float = 0.0, result_content=None):
        self._tool_name = tool_name
        self._delay = delay
        self._result_content = result_content or _make_mcp_result(
            content=[_make_text_content(f"result from {tool_name}")]
        )

    async def initialize(self):
        pass

    async def list_tools(self):
        from mcp import types
        return types.ListToolsResult(
            tools=[types.Tool(name=self._tool_name, inputSchema={})]
        )

    async def call_tool(self, name, arguments=None, **kwargs):
        await asyncio.sleep(self._delay)
        return self._result_content


class _FakeMcpToolbox(McpToolbox):
    """绕过 stdio_client，用 _FakeSession 替代真实 MCP 连接。"""

    _fake_sessions: dict = {}

    async def _serve(self, server_name: str) -> None:
        from agent_kernel.types import ToolSpec

        session = self._fake_sessions[server_name]

        response = await session.list_tools()
        local_tools: dict[str, ToolSpec] = {}
        local_remote_names: dict[str, tuple[str, str]] = {}
        for tool in response.tools:
            qualified = f"{server_name}.{tool.name}"
            local_tools[qualified] = ToolSpec(
                name=qualified, description=tool.description or "", parameters={}
            )
            local_remote_names[qualified] = (server_name, tool.name)

        with self._merge_lock:
            for qname in local_tools:
                if qname in self._tools_index:
                    raise ValueError(f"重复 MCP 工具名: {qname}")
            self._tools_index.update(local_tools)
            self._remote_names.update(local_remote_names)
            self._init_done_count += 1
            is_last = self._init_done_count == len(self.servers)

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
            self._tools = [ToolSpec(name=n, description="", parameters={})
                           for n in sorted(self.allow)]
            self._ready.set()

        while True:
            operation, payload, future = self._queues[server_name].get()
            if operation == "close":
                break
            try:
                name, args = payload
                _, remote_name = self._remote_names[name]
                result = await session.call_tool(remote_name, arguments=args)
                assert future is not None
                future.set_result(McpToolbox._map_result(result))
            except BaseException as exc:
                assert future is not None
                future.set_exception(exc)


def _make_fake_toolbox(slow_delay: float = 0.5):
    """构造一个两 server 的 fake toolbox：fast 无延迟，slow 有延迟。"""
    servers = {
        "fast": StdioServerConfig(command="echo", args=()),
        "slow": StdioServerConfig(command="echo", args=()),
    }
    toolbox = _FakeMcpToolbox(
        servers=servers,
        allow={"fast.tool", "slow.tool"},
    )
    toolbox._fake_sessions = {
        "fast": _FakeSession("tool", delay=0.0),
        "slow": _FakeSession("tool", delay=slow_delay),
    }
    return toolbox


def test_cross_server_concurrency():
    """慢 server 的调用不阻塞快 server 的调用（跨 server 并行）。

    两次调用并发发出：fast(0s) + slow(0.5s)。
    若串行，总时间 >= 0.5s（fast 先返回也要等 slow 排完）。
    若并行，fast 应在远小于 0.5s 内返回。
    """
    toolbox = _make_fake_toolbox(slow_delay=0.5)
    with toolbox:
        tools = toolbox.list_tools()
        assert {t.name for t in tools} == {"fast.tool", "slow.tool"}

        results = {}
        errors = {}

        def call_fast():
            try:
                results["fast"] = toolbox.call("fast.tool", {})
            except Exception as e:
                errors["fast"] = e

        def call_slow():
            try:
                results["slow"] = toolbox.call("slow.tool", {})
            except Exception as e:
                errors["slow"] = e

        t0 = time.monotonic()
        t_fast = threading.Thread(target=call_fast)
        t_slow = threading.Thread(target=call_slow)
        t_slow.start()
        time.sleep(0.05)  # 确保 slow 先入队
        t_fast.start()

        t_fast.join(timeout=2.0)
        t_fast_elapsed = time.monotonic() - t0
        t_slow.join(timeout=5.0)

    assert not errors, f"调用出错: {errors}"
    assert "fast" in results, "fast 调用未完成"
    assert "slow" in results, "slow 调用未完成"
    assert isinstance(results["fast"], ToolResult)
    assert isinstance(results["slow"], ToolResult)
    assert "result from tool" in results["fast"].content
    assert "result from tool" in results["slow"].content
    # fast 应在 0.5s 内返回（如果串行，fast 会被 slow 的 0.5s 延迟挡住）
    assert t_fast_elapsed < 0.5, (
        f"fast 调用耗时 {t_fast_elapsed:.3f}s，疑似被 slow server 串行阻塞"
    )


def test_same_server_calls_serialized():
    """同一 server 的两次调用仍然串行（MCP session 不支持并发调用）。"""
    delay = 0.3
    servers = {"only": StdioServerConfig(command="echo", args=())}

    class _MultiToolSession:
        """一个 server 上注册两个 tool，都有延迟，验证同 server 串行。"""

        async def initialize(self):
            pass

        async def list_tools(self):
            from mcp import types
            return types.ListToolsResult(
                tools=[
                    types.Tool(name="a", inputSchema={}),
                    types.Tool(name="b", inputSchema={}),
                ]
            )

        async def call_tool(self, name, arguments=None, **kwargs):
            await asyncio.sleep(delay)
            return _make_mcp_result(content=[_make_text_content(f"result from only.{name}")])

    toolbox = _FakeMcpToolbox(
        servers=servers,
        allow={"only.a", "only.b"},
    )
    toolbox._fake_sessions = {"only": _MultiToolSession()}

    with toolbox:
        results = {}
        threads = []

        def call(name):
            results[name] = toolbox.call(name, {})

        for name in ["only.a", "only.b"]:
            t = threading.Thread(target=call, args=(name,))
            threads.append(t)

        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        elapsed = time.monotonic() - t0

    assert len(results) == 2
    assert "result from only.a" in results["only.a"].content
    assert "result from only.b" in results["only.b"].content
    # 两次调用各 delay 秒，串行总耗时 >= 2*delay（允许一定调度抖动）
    assert elapsed >= 2 * delay - 0.1, (
        f"同 server 两次 {delay}s 调用总耗时仅 {elapsed:.3f}s，疑似未串行化"
    )


def test_list_tools_qualified_names():
    """list_tools 返回 server_name.tool.name 格式的限定名。"""
    toolbox = _make_fake_toolbox(slow_delay=0.0)
    with toolbox:
        names = {t.name for t in toolbox.list_tools()}
    assert names == {"fast.tool", "slow.tool"}


def test_startup_error_propagation():
    """某个 server 初始化失败时，__enter__ 抛 RuntimeError("MCP 初始化失败: ...")。"""

    class _FailingSession:
        async def initialize(self):
            raise ConnectionError("server down")

        async def list_tools(self):
            pass

    servers = {"bad": StdioServerConfig(command="nonexistent", args=())}
    toolbox = _FakeMcpToolbox(servers=servers, allow=set())
    toolbox._fake_sessions = {"bad": _FailingSession()}

    with pytest.raises(RuntimeError, match="MCP 初始化失败"):
        with toolbox:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
"""McpToolbox.search_tools 测试：子串匹配、回退全量、永不返回空集。

不需要真实 MCP server--通过子类绕过 _require_started 并手动填充 _tools，
直接测试 search_tools 的检索逻辑。

运行：PYTHONPATH=src python3 tests/test_mcp.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.tools.mcp import McpToolbox, StdioServerConfig
from agent_kernel.types import ToolSpec


def _make_spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters={})


class _SearchableMcpToolbox(McpToolbox):
    """绕过线程启动，直接测试 search_tools 逻辑。"""

    def __init__(self, tools: list[ToolSpec]) -> None:
        # 不调用父类 __init__（会要求 servers 非空），只设置 search_tools 需要的字段
        self._tools = list(tools)
        self._thread = None  # _require_started 会检查，但我们在 search_tools 前手动跳过

    def _require_started(self) -> None:
        pass  # 测试桩：不做线程检查


SAMPLE_TOOLS = [
    _make_spec("filesystem.read_text_file", "读取文件内容"),
    _make_spec("filesystem.list_directory", "列出目录下的文件"),
    _make_spec("filesystem.search_files", "按名称搜索文件"),
    _make_spec("fetch.fetch", "获取 URL 内容"),
    _make_spec("calc.calculate", "计算数学表达式"),
]


def test_search_tools_matches_name_substring():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("filesystem")
    names = [t.name for t in results]
    assert "filesystem.read_text_file" in names
    assert "filesystem.list_directory" in names
    assert "filesystem.search_files" in names
    assert "fetch.fetch" not in names
    assert "calc.calculate" not in names


def test_search_tools_matches_description_substring():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("数学")
    names = [t.name for t in results]
    assert names == ["calc.calculate"]


def test_search_tools_case_insensitive():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("FILESYSTEM")
    names = [t.name for t in results]
    assert len(names) == 3
    assert all(n.startswith("filesystem.") for n in names)


def test_search_tools_k_limits_results():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("filesystem", k=2)
    assert len(results) == 2


def test_search_tools_no_match_returns_full_list():
    """没有匹配时回退到全量列表，永不返回空集。"""
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("完全不存在的关键词xyz123")
    assert len(results) == len(SAMPLE_TOOLS)


def test_search_tools_no_match_with_k_returns_full_capped():
    """没有匹配且回退全量时，k 仍然生效。"""
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("完全不存在的关键词xyz123", k=2)
    assert len(results) == 2


def test_search_tools_empty_query_returns_all():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("")
    assert len(results) == len(SAMPLE_TOOLS)


def test_search_tools_empty_query_with_k():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("", k=3)
    assert len(results) == 3


def test_search_tools_k_none_returns_all_matches():
    box = _SearchableMcpToolbox(SAMPLE_TOOLS)
    results = box.search_tools("filesystem", k=None)
    assert len(results) == 3  # 所有 filesystem 工具，不截断


if __name__ == "__main__":
    test_search_tools_matches_name_substring()
    test_search_tools_matches_description_substring()
    test_search_tools_case_insensitive()
    test_search_tools_k_limits_results()
    test_search_tools_no_match_returns_full_list()
    test_search_tools_no_match_with_k_returns_full_capped()
    test_search_tools_empty_query_returns_all()
    test_search_tools_empty_query_with_k()
    test_search_tools_k_none_returns_all_matches()
    print("OK: MCP search_tools 测试全部通过")

