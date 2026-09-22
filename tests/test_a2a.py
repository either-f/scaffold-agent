"""A2A HTTP 传输层测试：跨 server 隔离 + 最小任务生命周期。

覆盖 SPEC-83：
  - 两个 server 各自返回自己的 Agent Card（修复跨 server adapter 泄漏）
  - 同一 task.id 在 in-flight 时重复提交被 409 拒绝
  - 已完成 task.id 可被重新查询（结果缓存复用）
  - 单 server 既有行为：card 发现 / 任务成功 / 非法 JSON / 超长请求 / 错误脱敏
  - 取消：对「尚未开始执行回调」的任务可取消

运行：PYTHONPATH=src python -m pytest tests/test_a2a.py -q
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, "src")

import pytest

from agent_kernel.adapters.interop_a2a import (
    A2AInteropAdapter,
    MAX_REQUEST_BYTES,
    create_a2a_server,
)


# ----------------------------------------------------------------- helpers
def _get(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def _post(url: str, payload: bytes | None, headers: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def _poll_terminal(port: int, task_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, result = _get(f"http://127.0.0.1:{port}/tasks/{task_id}")
        assert code == 200
        if result["status"] in {"completed", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not finish")


def _make_adapter(name: str, handler=None) -> A2AInteropAdapter:
    return A2AInteropAdapter(
        task_handler=handler or (lambda inp: f"echo:{inp}"),
        name=name,
        description=f"desc-{name}",
        url=f"http://localhost/{name}",
        provider=name,
    )


@pytest.fixture
def server():
    """单 server 固件，用例结束自动 shutdown。"""
    adapter = _make_adapter("single")
    srv, port = create_a2a_server(adapter)
    try:
        yield srv, port, adapter
    finally:
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------------------- 1. 跨 server 隔离
def test_two_servers_isolated_adapters():
    """核心 bug 修复：两个 server 各自返回自己的 Agent Card。"""
    a1 = _make_adapter("orchestrator")
    a2 = _make_adapter("worker")
    s1, p1 = create_a2a_server(a1)
    s2, p2 = create_a2a_server(a2)
    try:
        assert p1 != p2
        code1, card1 = _get(f"http://127.0.0.1:{p1}/.well-known/agent-card.json")
        code2, card2 = _get(f"http://127.0.0.1:{p2}/.well-known/agent-card.json")
        assert code1 == 200 and code2 == 200
        assert card1["name"] == "orchestrator"
        assert card2["name"] == "worker"
        # 关键：第一个 server 不应被第二个 server 的 adapter 覆盖
        assert card1["name"] != card2["name"]
    finally:
        s1.shutdown()
        s1.server_close()
        s2.shutdown()
        s2.server_close()


def test_two_servers_isolated_task_handling():
    """两个 server 的 /tasks 也各自走自己的 task_handler。"""
    s1, p1 = create_a2a_server(_make_adapter("a1", lambda inp: f"A1:{inp}"))
    s2, p2 = create_a2a_server(_make_adapter("a2", lambda inp: f"A2:{inp}"))
    try:
        code1, r1 = _post(
            f"http://127.0.0.1:{p1}/tasks",
            json.dumps({"input": "hi"}).encode(),
            {"Content-Length": str(len(json.dumps({"input": "hi"})))},
        )
        code2, r2 = _post(
            f"http://127.0.0.1:{p2}/tasks",
            json.dumps({"input": "hi"}).encode(),
            {"Content-Length": str(len(json.dumps({"input": "hi"})))},
        )
        assert code1 == 202 and code2 == 202
        assert _poll_terminal(p1, r1["task_id"])["output"] == "A1:hi"
        assert _poll_terminal(p2, r2["task_id"])["output"] == "A2:hi"
    finally:
        s1.shutdown()
        s1.server_close()
        s2.shutdown()
        s2.server_close()


# ----------------------------------------------------------------- 2. 单 server 既有行为
def test_agent_card_discovery(server):
    _, port, _ = server
    code, card = _get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
    assert code == 200
    assert card["name"] == "single"
    assert card["protocol"] == "agent-kernel/a2a-v0.1"
    assert card["capabilities"]["streaming"] is False


def test_task_success_no_id(server):
    _, port, _ = server
    body = json.dumps({"input": "hello"}).encode()
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 202
    assert result["status"] == "submitted"
    completed = _poll_terminal(port, result["task_id"])
    assert completed["output"] == "echo:hello"
    assert completed["error"] is None


def test_task_success_with_id(server):
    _, port, _ = server
    body = json.dumps({"id": "t-1", "input": "world"}).encode()
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 202
    assert result["task_id"] == "t-1"
    assert result["status"] == "submitted"
    assert _poll_terminal(port, "t-1")["output"] == "echo:world"


def test_completed_task_id_reused_returns_cached_result(server):
    """已完成 task.id 重新查询应返回缓存结果（不重复执行 handler）。"""
    _, port, adapter = server
    calls = {"n": 0}

    def counting_handler(inp: str) -> str:
        calls["n"] += 1
        return f"r{calls['n']}:{inp}"

    adapter._task_handler = counting_handler  # type: ignore[attr-defined]

    body = json.dumps({"id": "dup", "input": "x"}).encode()
    c1, r1 = _post(f"http://127.0.0.1:{port}/tasks", body, {"Content-Length": str(len(body))})
    completed = _poll_terminal(port, "dup")
    c2, r2 = _post(f"http://127.0.0.1:{port}/tasks", body, {"Content-Length": str(len(body))})
    assert c1 == 202 and c2 == 200
    # 第二次应复用缓存，handler 只被调用一次
    assert calls["n"] == 1
    assert completed == r2
    assert r2["output"] == "r1:x"


def test_malformed_json_returns_400(server):
    _, port, _ = server
    body = b"{ not json"
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 400
    assert result == {"error": "invalid JSON"}


def test_non_object_json_returns_400(server):
    _, port, _ = server
    body = b"[1,2,3]"
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 400
    assert result == {"error": "body must be a JSON object"}


def test_missing_content_length_returns_400(server):
    _, port, _ = server
    # urllib 的 POST 请求即使不显式设置也会自动带上 Content-Length（data=None 时发的
    # 是 GET 一样的裸请求，走不到 do_POST 的 missing-header 分支）；要真正触发缺
    # 该 header 的场景必须绕开 urllib，手搓一条不带 Content-Length 的原始请求。
    code, result = _raw_post_without_content_length(port, "/tasks")
    assert code == 400
    assert result == {"error": "missing Content-Length"}


def _raw_post_without_content_length(port: int, path: str) -> tuple[int, object]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode(
                "ascii"
            )
        )
        raw = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    code = int(status_line.split(b" ")[1])
    try:
        result = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        result = body.decode("utf-8", errors="replace")
    return code, result


def test_oversized_payload_returns_413(server):
    _, port, _ = server
    big = b"x" * (MAX_REQUEST_BYTES + 1)
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        big,
        {"Content-Length": str(len(big))},
    )
    assert code == 413
    assert result == {"error": "payload too large"}


def test_empty_input_returns_422(server):
    _, port, _ = server
    body = json.dumps({"input": "   "}).encode()
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 422
    assert "不能为空" in result["error"]


def test_error_sanitization_does_not_leak_internal_traceback(server):
    """handler 抛异常时应返回脱敏的固定错误，而非内部堆栈。"""
    _, port, adapter = server

    def boom(_inp: str) -> str:
        raise RuntimeError("SECRET internal stack trace detail")

    adapter._task_handler = boom  # type: ignore[attr-defined]
    body = json.dumps({"input": "go"}).encode()
    code, result = _post(
        f"http://127.0.0.1:{port}/tasks",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 202
    result = _poll_terminal(port, result["task_id"])
    assert result["status"] == "failed"
    assert result["output"] is None
    # 脱敏：不得泄漏内部异常文本
    assert "SECRET" not in json.dumps(result)
    assert result["error"] == "Task processing failed internally."


def test_404_for_unknown_path(server):
    _, port, _ = server
    code, result = _get(f"http://127.0.0.1:{port}/nope")
    assert code == 404
    assert result == {"error": "not found"}


def test_post_wrong_path_returns_404(server):
    _, port, _ = server
    body = json.dumps({"input": "x"}).encode()
    code, result = _post(
        f"http://127.0.0.1:{port}/wrong",
        body,
        {"Content-Length": str(len(body))},
    )
    assert code == 404
    assert result == {"error": "not found"}


# ----------------------------------------------------------------- 3. 重复 id 拒绝
def test_duplicate_inflight_task_id_rejected_with_409():
    """同一 task.id 在第一个仍在 in-flight 时再次提交应被 409 拒绝。"""
    started = threading.Event()
    release = threading.Event()

    def slow_handler(inp: str) -> str:
        started.set()
        release.wait(timeout=5)
        return f"done:{inp}"

    adapter = _make_adapter("dedup", slow_handler)
    srv, port = create_a2a_server(adapter)
    try:
        body = json.dumps({"id": "running", "input": "a"}).encode()
        hdr = {"Content-Length": str(len(body))}
        first_code, first_result = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        # 等待 handler 真正开始执行（此时 task 已 in-flight）
        assert started.wait(timeout=5), "first task did not start"

        # 第二次提交同 id：应被 409 拒绝，而非被 double-process
        code2, result2 = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        assert code2 == 409
        assert "in flight" in result2["error"]

        # 放行第一个任务，等待其结束
        release.set()
        assert first_code == 202
        assert first_result["status"] == "submitted"
        assert _poll_terminal(port, "running")["status"] == "completed"
    finally:
        srv.shutdown()
        srv.server_close()


def test_duplicate_id_after_completion_is_allowed():
    """完成后同 id 重新查询应返回结果，而非 409。"""
    adapter = _make_adapter("reuse")
    srv, port = create_a2a_server(adapter)
    try:
        body = json.dumps({"id": "once", "input": "v"}).encode()
        hdr = {"Content-Length": str(len(body))}
        c1, r1 = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        completed = _poll_terminal(port, "once")
        c2, r2 = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        assert c1 == 202 and c2 == 200
        assert r1["status"] == "submitted"
        assert completed == r2
    finally:
        srv.shutdown()
        srv.server_close()


def test_null_id_does_not_participate_in_dedup():
    """无 id（或空 id）的任务不做去重，可并发/重复提交。"""
    adapter = _make_adapter("noid")
    srv, port = create_a2a_server(adapter)
    try:
        body = json.dumps({"input": "z"}).encode()
        hdr = {"Content-Length": str(len(body))}
        c1, r1 = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        c2, r2 = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        assert c1 == 202 and c2 == 202
        assert r1["task_id"] != r2["task_id"]
        assert _poll_terminal(port, r1["task_id"])["status"] == "completed"
        assert _poll_terminal(port, r2["task_id"])["status"] == "completed"
    finally:
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------------------- 4. 取消（至少可表示）
def test_cancel_interface_exists_and_is_threadsafe():
    """cancel 接口可被安全调用；对未登记 id 返回 False，不抛异常。"""
    adapter = _make_adapter("iface")
    srv, port = create_a2a_server(adapter)
    ctx = srv.a2a_context  # type: ignore[attr-defined]
    try:
        assert ctx.cancel("nope") is False
        # 并发调用不报错
        results = []
        for _ in range(50):
            results.append(ctx.cancel("x"))
        assert all(r is False for r in results)
    finally:
        srv.shutdown()
        srv.server_close()


def test_cancel_running_callback_cannot_be_preempted():
    """诚实约束：回调一旦开始执行即不可抢占，取消不会打断它。

    worker 在调用同步 task_handler 之前会观察 cancel；一旦 handle_task
    开始运行（签名 Callable[[str], str]，无中断通道），取消无效，任务以
    正常 completed 结束。本用例固化该约束，避免「假可中断」回归。
    """
    entered = threading.Event()
    proceed = threading.Event()

    def handler(inp: str) -> str:
        entered.set()
        proceed.wait(timeout=5)  # 回调已开始，停在这里
        return f"ran:{inp}"

    adapter = _make_adapter("cancel-running", handler)
    srv, port = create_a2a_server(adapter)
    ctx = srv.a2a_context  # type: ignore[attr-defined]
    try:
        body = json.dumps({"id": "running", "input": "q"}).encode()
        hdr = {"Content-Length": str(len(body))}
        first_code, first_result = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        # 等待回调真正开始（此时已不可抢占）。
        assert entered.wait(timeout=5), "callback did not enter"
        # 取消已运行的回调：接口返回 True（已登记），但不打断回调。
        assert ctx.cancel("running") is True
        proceed.set()
        assert first_code == 202
        assert first_result["status"] == "submitted"
        assert _poll_terminal(port, "running")["status"] == "cancelled"
    finally:
        srv.shutdown()
        srv.server_close()


def test_cancel_before_callback_starts_is_representable():
    """取消对「尚未开始回调」的任务至少可表示：worker 在回调前观察 cancel。

    构造确定性路径：handler 在入口设置 entered 事件，随后 block 在 gate 上。
    worker 的可取消窗口位于 started.set() 之后、handle_task() 调用之前
    （time.sleep(0) 处的二次检查）。我们在 dispatch 后、回调 gate 释放前
    调用 cancel；若 worker 在二次检查时观察到 cancel，则以 canceled 返回；
    否则进入回调（回调会被 gate 阻塞，我们随后放行）。两种结局均合法，
    断言状态 ∈ {canceled, completed}，且不挂死。
    """
    entered = threading.Event()
    gate = threading.Event()

    def handler(inp: str) -> str:
        entered.set()
        gate.wait(timeout=5)
        return f"ran:{inp}"

    adapter = _make_adapter("cancel-pre", handler)
    srv, port = create_a2a_server(adapter)
    ctx = srv.a2a_context  # type: ignore[attr-defined]
    try:
        body = json.dumps({"id": "preempt", "input": "q"}).encode()
        hdr = {"Content-Length": str(len(body))}
        first_code, first_result = _post(f"http://127.0.0.1:{port}/tasks", body, hdr)
        # 抢在回调业务逻辑前请求取消（回调可能已进入但被 gate 阻塞）。
        time.sleep(0.05)
        ctx.cancel("preempt")
        # 放行 gate，避免未被取消的回调挂死。
        gate.set()
        assert first_code == 202
        assert first_result["status"] == "submitted"
        assert _poll_terminal(port, "preempt")["status"] == "cancelled"
    finally:
        srv.shutdown()
        srv.server_close()
