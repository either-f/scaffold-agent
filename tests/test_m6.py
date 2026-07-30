"""M6: Orchestrator-worker 委派测试 + A2A 互操作适配器测试。

WorkerDelegationPort：工具发现、成功委派、未知 worker 拒绝、失败 worker 处理、
父 agent fake-model 端到端委派。
A2AInteropAdapter：Agent Card 发现、成功任务、格式错误/超大请求、handler 失败消毒、接口一致性。
运行：PYTHONPATH=src python3 tests/test_m6.py   （也兼容 pytest）
"""
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")

from agent_kernel.adapters.interop_a2a import (
    A2AInteropAdapter,
    create_a2a_server,
)
from agent_kernel.adapters.memory_graph import GraphMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.tools_agents import WorkerDelegationPort
from agent_kernel.adapters.tools_local import LocalToolbox, default_toolbox
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.ports import InteropPort


def test_discovery():
    inner = LocalToolbox()
    inner.register("now", "获取当前时间", lambda: "2024-01-01")
    workers = WorkerDelegationPort(inner)

    wk = AgentKernel(
        model=FakeScriptedModel(['{"thought": "t", "final": "worker result"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    workers.register("researcher", wk, "研究型 worker，负责信息检索与分析")

    tools = workers.list_tools()
    names = {t.name for t in tools}
    assert "now" in names
    assert "worker_researcher" in names

    spec = next(t for t in tools if t.name == "worker_researcher")
    assert spec.description == "研究型 worker，负责信息检索与分析"
    assert "task" in spec.parameters.get("properties", {})
    assert spec.parameters["properties"]["task"]["type"] == "string"
    assert "task" in spec.parameters.get("required", [])


def test_successful_delegation():
    inner = LocalToolbox()
    inner.register("now", "获取当前时间", lambda: "2024-01-01")
    workers = WorkerDelegationPort(inner)

    wk = AgentKernel(
        model=FakeScriptedModel(['{"thought": "分析完成", "final": "研究结果：X 比 Y 好，因为 X 更快"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    workers.register("researcher", wk, "研究 worker")

    result = workers.call("worker_researcher", {"task": "比较 X 和 Y 的性能"})
    assert "X 比 Y 好" in result

    assert workers.call("now", {}) == "2024-01-01"


def test_unknown_worker_rejection():
    inner = LocalToolbox()
    workers = WorkerDelegationPort(inner)

    try:
        workers.call("worker_ghost", {"task": "do something"})
        raise AssertionError("应拒绝未知 worker 调用")
    except KeyError:
        pass

    assert len(workers.list_tools()) == 0


def test_failed_worker_handling():
    inner = LocalToolbox()
    workers = WorkerDelegationPort(inner)

    wk = AgentKernel(
        model=FakeScriptedModel([
            '{"thought": "t", "tool": "calc", "args": {"expression": "1+1"}}',
        ]),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        max_steps=1,
    )
    workers.register("doomed", wk, "注定失败的 worker")

    try:
        workers.call("worker_doomed", {"task": "算点东西"})
        raise AssertionError("应抛出 RuntimeError")
    except RuntimeError as e:
        assert "failed" in str(e).lower()


def test_parent_e2e_delegation():
    inner = default_toolbox()
    workers = WorkerDelegationPort(inner)

    wk = AgentKernel(
        model=FakeScriptedModel(['{"thought": "检索", "final": "检索结果：Python 3.12 已正式发布"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )
    workers.register("searcher", wk, "搜索最新信息")

    parent = AgentKernel(
        model=FakeScriptedModel([
            '{"thought": "委派搜索", "tool": "worker_searcher", "args": {"task": "搜索最新 Python 版本"}}',
            '{"thought": "总结", "final": "根据搜索结果，Python 3.12 已发布"}',
        ]),
        tools=workers,
        planner=ReactPlanner(),
    )
    state = parent.run("查询 Python 最新版本")
    assert state.status == "done"
    assert "3.12" in (state.answer or "")

    tool_msgs = [m for m in state.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "3.12" in tool_msgs[0].content


# ========================================================================== A2A 互操作测试
def _make_adapter() -> A2AInteropAdapter:
    def handler(msg: str) -> str:
        return f"processed: {msg}"

    return A2AInteropAdapter(
        task_handler=handler,
        name="test-agent",
        description="测试 agent",
        url="http://127.0.0.1:0",
    )


def _make_server(adapter=None):
    if adapter is None:
        adapter = _make_adapter()
    server, port = create_a2a_server(adapter)
    return server, port, adapter


def _http_get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def _http_post(url: str, body: dict | list | str | bytes) -> tuple[int, dict]:
    if isinstance(body, bytes):
        data = body
    elif isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ------------------------------------------------------------------ 接口一致性
def test_interface_conformance():
    adapter = _make_adapter()
    assert isinstance(adapter, InteropPort)

    card = adapter.agent_card()
    assert isinstance(card, dict)
    assert card["name"] == "test-agent"
    assert card["description"] == "测试 agent"
    assert "protocol" in card
    assert "capabilities" in card

    result = adapter.handle_task({"id": "t1", "input": "hello"})
    assert result["task_id"] == "t1"
    assert result["status"] == "completed"
    assert result["output"] == "processed: hello"
    assert result["error"] is None


# ------------------------------------------------------------------ Agent Card 发现
def test_card_discovery():
    server, port, _ = _make_server()
    try:
        status, body = _http_get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
        assert status == 200
        assert body["name"] == "test-agent"
        assert "protocol" in body
        assert "capabilities" in body
        assert isinstance(body["capabilities"], dict)
    finally:
        server.shutdown()


# ------------------------------------------------------------------ 成功任务
def test_successful_task():
    server, port, _ = _make_server()
    try:
        status, body = _http_post(
            f"http://127.0.0.1:{port}/tasks",
            {"id": "task-1", "input": "帮我查一下天气"},
        )
        assert status == 200
        assert body["task_id"] == "task-1"
        assert body["status"] == "completed"
        assert body["output"] == "processed: 帮我查一下天气"
        assert body["error"] is None
    finally:
        server.shutdown()


# ------------------------------------------------------------------ 格式错误请求
def test_malformed_json_request():
    server, port, _ = _make_server()
    try:
        status, body = _http_post(f"http://127.0.0.1:{port}/tasks", "not json")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()


def test_invalid_task_input():
    server, port, _ = _make_server()
    try:
        status, body = _http_post(f"http://127.0.0.1:{port}/tasks", {"id": "t1", "input": ""})
        assert status == 422
        assert "error" in body
    finally:
        server.shutdown()


def test_non_dict_body():
    server, port, _ = _make_server()
    try:
        status, body = _http_post(f"http://127.0.0.1:{port}/tasks", [1, 2, 3])
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()


# ------------------------------------------------------------------ 超大请求
def test_oversized_request():
    import http.client
    server, port, _ = _make_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/tasks")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(2_000_000))
        conn.endheaders()
        conn.send(b'{"id":"t","input":"x"}')
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        assert resp.status == 413
        assert "error" in body
    finally:
        server.shutdown()


def test_repeatable_oversized_rejection():
    """同一连接连续超大请求均返回 413，不产生连接异常。"""
    import http.client
    server, port, _ = _make_server()
    try:
        for _ in range(3):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.putrequest("POST", "/tasks")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(3_000_000))
            conn.endheaders()
            conn.send(b"{}")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            assert resp.status == 413
            assert "error" in body
    finally:
        server.shutdown()


def test_missing_content_length():
    import http.client
    server, port, _ = _make_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/tasks")
        conn.putheader("Content-Type", "application/json")
        conn.endheaders()
        conn.send(b'{"id":"t","input":"hello world"}')
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        assert resp.status == 400
        assert "error" in body
    finally:
        server.shutdown()


def test_non_integer_content_length():
    import http.client
    server, port, _ = _make_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/tasks")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        conn.send(b"{}")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        assert resp.status == 400
        assert "error" in body
    finally:
        server.shutdown()


def test_negative_content_length():
    import http.client
    server, port, _ = _make_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/tasks")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "-1")
        conn.endheaders()
        conn.send(b"{}")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        assert resp.status == 400
        assert "error" in body
    finally:
        server.shutdown()


def test_ephemeral_port_binding():
    """未指定端口时直接绑定 0，server_port 返回实际端口。"""
    adapter = _make_adapter()
    server, port = create_a2a_server(adapter)
    try:
        assert isinstance(port, int)
        assert port > 0
        assert port <= 65535

        status, body = _http_get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
        assert status == 200
        assert body["name"] == "test-agent"
    finally:
        server.shutdown()


# ------------------------------------------------------------------ handler 失败消毒
def test_handler_failure_sanitization():
    def failing_handler(msg: str) -> str:
        raise RuntimeError("数据库连接超时: secret_password=abc123")

    adapter = A2AInteropAdapter(task_handler=failing_handler)
    result = adapter.handle_task({"id": "t1", "input": "hello"})
    assert result["status"] == "failed"
    assert result["error"] == "Task processing failed internally."
    assert result["output"] is None
    assert "secret_password" not in result["error"]
    assert "数据库连接超时" not in result["error"]


# ------------------------------------------------------------------ 外部进程发现与提交
def test_external_process_discovery_and_submit():
    """验证：其他本地进程可以发现 card 并提交任务，且不导入内核内部。"""
    wk = AgentKernel(
        model=FakeScriptedModel(['{"thought": "检索完成", "final": "X 比 Y 快 2.3 倍"}']),
        tools=default_toolbox(),
        planner=ReactPlanner(),
    )

    adapter = A2AInteropAdapter(
        task_handler=lambda msg: wk.run(msg).answer or "",
        name="researcher",
        url="http://127.0.0.1:0",
    )
    server, port = create_a2a_server(adapter)
    try:
        # --- 用另一个"进程"（线程模拟）通过 HTTP 调用 ---
        results: list = []
        error_ref: list[Exception] = []

        def client() -> None:
            try:
                status, card = _http_get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
                results.append(("card_status", status))
                results.append(("card_name", card["name"]))

                status, task = _http_post(
                    f"http://127.0.0.1:{port}/tasks",
                    {"id": "ext-1", "input": "比较 X 和 Y 的性能"},
                )
                results.append(("task_status", status))
                results.append(("task_output", task["output"]))
                results.append(("task_result_status", task["status"]))
            except Exception as e:
                error_ref.append(e)

        t = threading.Thread(target=client)
        t.start()
        t.join(timeout=10)

        if error_ref:
            raise error_ref[0]

        assert ("card_status", 200) in results
        assert ("card_name", "researcher") in results
        assert ("task_status", 200) in results
        assert ("task_result_status", "completed") in results
        assert any("2.3 倍" in (str(v) if v is not None else "") for _, v in results)
    finally:
        server.shutdown()


# ===================================================================== SPEC-63: Graph memory adapter tests

def test_graph_memory_add_and_search():
    """基本 add/search 符合 MemoryPort 契约。"""
    mem = GraphMemory(":memory:")
    mem.add("run-1", "user", "Python 是动态类型语言")
    mem.add("run-1", "assistant", "Python 支持面向对象编程")
    mem.add("run-1", "user", "Rust 是静态类型语言")

    results = mem.search("Python", k=5)
    assert len(results) >= 2
    assert any("面向对象" in r for r in results)
    assert any("动态类型" in r for r in results)

    results2 = mem.search("Rust", k=5)
    assert len(results2) == 1
    assert "静态类型" in results2[0]

    assert mem.search("", k=5) == []
    assert mem.search("Python", k=0) == []


def test_graph_memory_persistence_across_reopen():
    """数据库文件持久化：关闭后重新打开，数据仍在。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "graph.db"

        mem = GraphMemory(str(db_path))
        mem.add("run-1", "user", "持久化测试事实")
        mem.add_edge("ns", "Alice", "knows", "Bob")
        mem.conn.close()
        del mem

        mem2 = GraphMemory(str(db_path))
        results = mem2.search("持久化", k=5)
        assert len(results) == 1
        assert "持久化测试事实" in results[0]

        edges = mem2.search_edges("ns", subject="Alice")
        assert len(edges) == 1
        assert edges[0] == ("Alice", "knows", "Bob")
        mem2.conn.close()


def test_graph_memory_edge_add_and_search():
    """显式边增删查。"""
    mem = GraphMemory(":memory:")
    mem.add_edge("ns1", "Alice", "likes", "Python")
    mem.add_edge("ns1", "Alice", "knows", "Bob")
    mem.add_edge("ns1", "Bob", "knows", "Charlie")
    mem.add_edge("ns1", "Charlie", "likes", "Python")

    edges = mem.search_edges("ns1", subject="Alice")
    assert len(edges) == 2
    names = {(s, r, o) for s, r, o in edges}
    assert ("Alice", "likes", "Python") in names
    assert ("Alice", "knows", "Bob") in names

    edges2 = mem.search_edges("ns1", relation="likes")
    assert len(edges2) == 2
    assert all(r == "likes" for _, r, _ in edges2)

    edges3 = mem.search_edges("ns1", object="Python")
    assert len(edges3) == 2
    assert all(o == "Python" for _, _, o in edges3)

    edges4 = mem.search_edges("ns1", subject="Alice", relation="knows")
    assert edges4 == [("Alice", "knows", "Bob")]


def test_graph_memory_neighbors():
    """邻居遍历。"""
    mem = GraphMemory(":memory:")
    mem.add_edge("ns", "Alice", "knows", "Bob")
    mem.add_edge("ns", "Bob", "likes", "Python")
    mem.add_edge("ns", "Charlie", "knows", "Alice")

    out = mem.get_neighbors("ns", "Alice", direction="outgoing")
    assert len(out) == 1
    assert out[0][:3] == ("Alice", "knows", "Bob")

    incoming = mem.get_neighbors("ns", "Alice", direction="incoming")
    assert len(incoming) == 1
    assert incoming[0][:3] == ("Charlie", "knows", "Alice")

    both = mem.get_neighbors("ns", "Alice", direction="both")
    assert len(both) == 2

    try:
        mem.get_neighbors("ns", "Alice", direction="wrong")
        raise AssertionError("无效方向应拒绝")
    except ValueError:
        pass


def test_graph_memory_namespace_isolation():
    """不同 namespace 的事实和边完全隔离。"""
    mem = GraphMemory(":memory:")
    mem.add("run-a", "user", "命名空间 A 的事实")
    mem.add("run-b", "user", "命名空间 B 的事实")
    mem.add_edge("nsA", "X", "rel", "Y")
    mem.add_edge("nsB", "X", "rel", "Z")

    # 事实 —— 都在 default namespace 下
    results = mem.search("命名空间", k=10)
    assert len(results) == 2

    # 边 —— 不同 namespace 隔离
    edges_a = mem.search_edges("nsA")
    assert len(edges_a) == 1
    assert edges_a[0] == ("X", "rel", "Y")

    edges_b = mem.search_edges("nsB")
    assert len(edges_b) == 1
    assert edges_b[0] == ("X", "rel", "Z")

    # 查询不存在的 namespace：空列表
    assert mem.search_edges("ghost") == []


def test_graph_memory_deduplication():
    """相同内容去重：同一 run/role/content 只存一条。"""
    mem = GraphMemory(":memory:")
    mem.add("run-1", "user", "重复事实")
    mem.add("run-1", "user", "重复事实")
    mem.add("run-1", "user", "另一条")

    results = mem.search("重复", k=5)
    assert len(results) == 1, "重复事实应去重只保留一条"
    assert "重复事实" in results[0]

    results2 = mem.search("另一", k=5)
    assert len(results2) == 1

    # 边去重
    mem.add_edge("ns", "A", "rel", "B")
    mem.add_edge("ns", "A", "rel", "B")
    edges = mem.search_edges("ns")
    assert len(edges) == 1, "重复边应去重"


def test_graph_memory_role_filtering():
    """tool-role 写入被忽略，对齐 PgVectorMemory 行为。"""
    mem = GraphMemory(":memory:")
    mem.add("run-1", "tool", "calc: 49")
    mem.add("run-1", "user", "有效事实")
    mem.add("run-1", "assistant", "有效回答")

    results = mem.search("有效", k=10)
    assert len(results) == 2, "tool role 不被存储，只有 user/assistant"

    for r in results:
        assert "calc" not in r, "tool 内容不应出现"

    # 空格内容也被忽略
    mem.add("run-1", "user", "   ")
    results2 = mem.search("有效", k=10)
    assert len(results2) == 2


def test_graph_memory_invalid_edges():
    """空/无效边参数应抛出 ValueError。"""
    mem = GraphMemory(":memory:")
    for ns, subj, rel, obj in [
        ("", "A", "r", "B"),
        ("ns", "", "r", "B"),
        ("ns", "A", "", "B"),
        ("ns", "A", "r", ""),
        ("   ", "A", "r", "B"),
    ]:
        try:
            mem.add_edge(ns, subj, rel, obj)
            raise AssertionError(f"应拒绝空参数: {(ns, subj, rel, obj)}")
        except ValueError:
            pass


def test_graph_memory_with_agent_kernel():
    """GraphMemory 替换其他 MemoryPort 无需修改 kernel。"""
    mem = GraphMemory(":memory:")

    model = FakeScriptedModel([
        '{"thought": "检索记忆", "tool": "calc", "args": {"expression": "2+3"}}',
        '{"thought": "总结", "final": "计算结果是 5"}',
    ])
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=ReactPlanner(),
        memory=mem,
    )
    state = kernel.run("计算 2+3")
    assert state.status == "done"
    assert "5" in (state.answer or "")

    # user 输入和 assistant 最终回复都进了记忆
    results = mem.search("计算", k=10)
    assert len(results) >= 1, f"记忆应有内容，实际: {results}"

    # tool 调用不应入记忆（被 role filtering 忽略）
    tool_results = mem.search("calc", k=10)
    assert tool_results == []


def test_graph_memory_empty_query_search():
    """空查询 / 空白查询返回空列表。"""
    mem = GraphMemory(":memory:")
    mem.add("run-1", "user", "hello")
    assert mem.search("", k=5) == []
    assert mem.search("   ", k=5) == []


if __name__ == "__main__":
    test_discovery()
    test_successful_delegation()
    test_unknown_worker_rejection()
    test_failed_worker_handling()
    test_parent_e2e_delegation()
    test_interface_conformance()
    test_card_discovery()
    test_successful_task()
    test_malformed_json_request()
    test_invalid_task_input()
    test_non_dict_body()
    test_oversized_request()
    test_repeatable_oversized_rejection()
    test_missing_content_length()
    test_non_integer_content_length()
    test_negative_content_length()
    test_ephemeral_port_binding()
    test_handler_failure_sanitization()
    test_external_process_discovery_and_submit()
    test_graph_memory_add_and_search()
    test_graph_memory_persistence_across_reopen()
    test_graph_memory_edge_add_and_search()
    test_graph_memory_neighbors()
    test_graph_memory_namespace_isolation()
    test_graph_memory_deduplication()
    test_graph_memory_role_filtering()
    test_graph_memory_invalid_edges()
    test_graph_memory_with_agent_kernel()
    test_graph_memory_empty_query_search()
    print("OK: 全部 M6 测试通过")
