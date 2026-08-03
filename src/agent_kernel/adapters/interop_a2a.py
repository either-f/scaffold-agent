"""A2A 互操作适配器：Agent Card + 同步任务处理 + 最小 HTTP 传输层。

融合纪律：内核零第三方依赖；本模块仅使用标准库 http.server。
可选依赖 a2a-sdk 留到 pyproject.toml 的 [a2a] extra，CI 不要求。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable

from ..ports import InteropPort

MAX_REQUEST_BYTES = 1_048_576  # 1 MB
MAX_TRACKED_TASKS = 4096  # 每个 server 实例的任务登记上限，防无界增长


class A2AInteropAdapter(InteropPort):
    """A2A 互操作适配器：生成 Agent Card 并同步处理任务。"""

    def __init__(
        self,
        task_handler: Callable[[str], str],
        *,
        name: str = "agent-kernel",
        description: str = "通用 agent 内核（ReAct + 工具）",
        version: str = "0.1.0",
        url: str = "http://localhost:8080",
        provider: str = "agent-kernel",
    ) -> None:
        self._task_handler = task_handler
        self._name = name
        self._description = description
        self._version = version
        self._url = url
        self._provider = provider

    # ------------------------------------------------------------------ InteropPort
    def agent_card(self) -> dict:
        return {
            "protocol": "agent-kernel/a2a-v0.1",
            "name": self._name,
            "description": self._description,
            "version": self._version,
            "url": self._url,
            "provider": {"name": self._provider},
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
            },
            "skillDescriptions": [],
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
        }

    def handle_task(self, task: dict) -> dict:
        if not isinstance(task, dict):
            raise TypeError("task 必须是 dict")
        raw_id = task.get("id")
        if raw_id is not None and not isinstance(raw_id, str):
            raise TypeError("task.id 必须是 string 或 null")
        task_id = raw_id or "unknown"
        user_input = task.get("input")
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("task.input 不能为空")
        try:
            output = self._task_handler(user_input)
        except Exception:
            return {
                "task_id": task_id,
                "status": "failed",
                "output": None,
                "error": "Task processing failed internally.",
            }
        return {
            "task_id": task_id,
            "status": "completed",
            "output": output,
            "error": None,
        }


# -------------------------------------------------------------------- HTTP 传输
class _A2AHTTPHandler(BaseHTTPRequestHandler):
    adapter: A2AInteropAdapter
    # 每个 server 实例独立的生命周期上下文，由 create_a2a_server 通过
    # 动态子类的类属性注入，避免跨 server 共享 _A2AHTTPHandler 基类属性。
    ctx: "_ServerContext"

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        pass  # 静默，避免测试输出掺杂日志

    def _send_json(self, code: int, body: dict | list) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _send_error_json(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})

    def _drain_oversized_body(self, max_bytes: int = 65536, timeout: float = 1.0) -> None:
        """Drain up to max_bytes from the request body with a short timeout.

        On Windows, closing a socket with unread client data can trigger a TCP RST
        that aborts the 413 response delivery.  Draining a bounded prefix after the
        response ensures the client reliably receives the 413 before the close.
        """
        try:
            old_timeout = self.connection.gettimeout()
        except OSError:
            old_timeout = None
        try:
            self.connection.settimeout(timeout)
            drained = 0
            while drained < max_bytes:
                chunk_size = min(8192, max_bytes - drained)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass
        finally:
            try:
                self.connection.settimeout(old_timeout)
            except OSError:
                pass

    # --------------------------------------------------------------- routes
    def do_GET(self) -> None:
        if self.path == "/.well-known/agent-card.json":
            self._send_json(200, self.adapter.agent_card())
        else:
            self._send_error_json(404, "not found")

    def do_POST(self) -> None:
        body_consumed = False
        try:
            if self.path != "/tasks":
                self._send_error_json(404, "not found")
                return

            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send_error_json(400, "missing Content-Length")
                return

            try:
                length = int(raw_length)
            except ValueError:
                self._send_error_json(400, "invalid Content-Length")
                return

            if length < 0:
                self._send_error_json(400, "invalid Content-Length")
                return

            if length > MAX_REQUEST_BYTES:
                self._send_error_json(413, "payload too large")
                self.close_connection = True
                return

            raw = self.rfile.read(length)
            body_consumed = True
            try:
                task = json.loads(raw)
            except json.JSONDecodeError:
                self._send_error_json(400, "invalid JSON")
                return

            if not isinstance(task, dict):
                self._send_error_json(400, "body must be a JSON object")
                return

            # ---- 生命周期：重复 id / 结果复用 / 取消 ----
            raw_id = task.get("id")
            has_explicit_id = isinstance(raw_id, str) and raw_id != ""

            if has_explicit_id:
                task_id = raw_id  # type: ignore[assignment]
                # 1) 仍在 in-flight：拒绝重复提交（409）
                if self.ctx.is_inflight(task_id):
                    self._send_error_json(409, f"task already in flight: {task_id}")
                    return
                # 2) 已完成且结果仍在缓存：直接复用结果（允许同 id 重新查询）
                cached = self.ctx.get_completed(task_id)
                if cached is not None:
                    self._send_json(200, cached)
                    return
            else:
                task_id = None  # 无 id：不做去重/缓存

            # 3) 派发到 worker 线程，支持「尚未开始」的取消；handler 线程
            #    在等待期间以短轮询方式响应取消请求。
            try:
                result = self.ctx.dispatch_and_wait(
                    self.adapter,
                    task,
                    task_id,
                )
            except (TypeError, ValueError) as e:
                self._send_error_json(422, str(e))
                return

            # dispatch_and_wait 在被取消时返回 status=="canceled"
            self._send_json(200, result)
        finally:
            if not body_consumed:
                self._drain_oversized_body()


class _ServerContext:
    """单个 A2A server 实例的任务生命周期上下文（线程安全、有界）。

    - inflight: 已派发但尚未结束（completed/failed/canceled）的 task id 集合。
    - completed: 已结束任务的最终结果缓存（供同 id 重新查询），LRU 式有界。
    - 取消语义：cancel(task_id) 设置一个 threading.Event。worker 线程在真正
      调用 adapter 的同步 task_handler **之前** 检查该事件；一旦已经开始执行
      同步回调即无法抢占（见 dispatch_and_wait 的注释）。
    """

    def __init__(self, adapter: A2AInteropAdapter, max_tracked: int = MAX_TRACKED_TASKS) -> None:
        self.adapter = adapter
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}  # task_id -> done event
        self._results: dict[str, dict] = {}  # task_id -> 最终 result（有界 LRU）
        self._cancel_events: dict[str, threading.Event] = {}
        self._max_tracked = max_tracked

    # ---------------------------------------------------------- 查询
    def is_inflight(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._inflight

    def get_completed(self, task_id: str) -> dict | None:
        with self._lock:
            res = self._results.get(task_id)
            # 触发 LRU 顺序更新（dict 保持插入序，删后重插即移到末尾）
            if res is not None:
                self._results.pop(task_id, None)
                self._results[task_id] = res
            return res

    # ---------------------------------------------------------- 登记/清理
    def _register(self, task_id: str) -> threading.Event:
        done = threading.Event()
        cancel = threading.Event()
        with self._lock:
            self._evict_if_needed_locked()
            self._inflight[task_id] = done
            self._cancel_events[task_id] = cancel
        return done, cancel  # type: ignore[return-value]

    def _evict_if_needed_locked(self) -> None:
        # 同时约束 inflight 与 results 两个结构，任一超限按 FIFO 淘汰最旧。
        while len(self._inflight) > self._max_tracked:
            old_id = next(iter(self._inflight))
            self._inflight.pop(old_id, None)
            self._cancel_events.pop(old_id, None)
        while len(self._results) > self._max_tracked:
            old_id = next(iter(self._results))
            self._results.pop(old_id, None)

    def _finish(self, task_id: str, result: dict) -> None:
        with self._lock:
            self._inflight.pop(task_id, None)
            self._cancel_events.pop(task_id, None)
            self._evict_if_needed_locked()
            self._results[task_id] = result

    # ---------------------------------------------------------- 取消
    def cancel(self, task_id: str) -> bool:
        """请求取消一个 in-flight 任务。

        返回 True 表示已登记取消请求；False 表示该 id 当前不在 in-flight 中。
        注意：若 worker 已开始执行同步 task_handler，本请求不会打断它，
        任务仍会以正常结果结束（取消仅对「尚未开始执行回调」的任务生效）。
        """
        with self._lock:
            ev = self._cancel_events.get(task_id)
            if ev is None:
                return False
            ev.set()
            return True

    # ---------------------------------------------------------- 派发 + 等待
    def dispatch_and_wait(
        self,
        adapter: A2AInteropAdapter,
        task: dict,
        task_id: str | None,
    ) -> dict:
        """在 worker 线程中执行 adapter.handle_task，handler 线程等待其完成。

        取消语义（诚实约束）：
          - worker 在调用同步 task_handler 之前会检查 cancel 事件，若已被取消
            则不执行回调，直接返回 status=="canceled"。
          - 一旦同步 task_handler 开始执行即无法抢占（回调签名为
            Callable[[str], str]，不接受中断信号）；此时即便收到取消请求，
            任务仍会以正常结果完成。我们不做「假可中断」。
          - handler 线程在等待 done 事件时使用短超时轮询，使得「派发后、回调
            执行前」窗口内的取消请求能被 worker 观察到并生效。
        """
        if task_id is None:
            # 无 id：直接同步执行，不做生命周期登记（保持旧行为）。
            return self._run_adapter(adapter, task)

        done, cancel = self._register(task_id)
        started = threading.Event()
        # holder: result 为最终结果；exc 为形参校验类异常（供 handler 侧回 422）。
        holder: dict[str, object] = {"result": None, "exc": None}

        def _worker() -> None:
            # 在真正调用同步回调前，给取消请求一个被观察到的机会。
            # 这里用短轮询让 handler 线程的等待也能及时退出。
            if cancel.is_set():
                holder["result"] = {
                    "task_id": task_id,
                    "status": "canceled",
                    "output": None,
                    "error": None,
                }
                started.set()
                done.set()
                return
            # 标记「即将进入回调」；在此之后再观察一次取消请求，
            # 使得「派发后、回调真正执行前」窗口内的取消仍可生效。
            started.set()
            # 让出一次调度，给并发的 cancel() 一个被观察到的机会。
            time.sleep(0)
            if cancel.is_set():
                holder["result"] = {
                    "task_id": task_id,
                    "status": "canceled",
                    "output": None,
                    "error": None,
                }
                done.set()
                return
            try:
                holder["result"] = adapter.handle_task(task)
            except (TypeError, ValueError) as e:
                # 形参校验类异常：交给 handler 线程以 422 回应。
                holder["exc"] = e
            except Exception:
                holder["result"] = {
                    "task_id": task_id,
                    "status": "failed",
                    "output": None,
                    "error": "Task processing failed internally.",
                }
            done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        # handler 线程等待；短轮询让取消在「worker 尚未进入回调」时仍可生效。
        while not done.wait(timeout=0.05):
            # 等待期间收到取消请求：仅当 worker 尚未开始回调时才生效。
            if cancel.is_set() and not started.is_set():
                # worker 会在下一轮检查 cancel 并返回 canceled；继续等待其结束。
                continue

        exc = holder["exc"]
        if exc is not None:
            # 校验失败：不缓存结果，移出 inflight，交回 handler 侧抛 422。
            with self._lock:
                self._inflight.pop(task_id, None)
                self._cancel_events.pop(task_id, None)
            assert isinstance(exc, (TypeError, ValueError))
            raise exc

        result = holder["result"]  # type: ignore[assignment]
        assert result is not None
        self._finish(task_id, result)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]

    @staticmethod
    def _run_adapter(adapter: A2AInteropAdapter, task: dict) -> dict:
        try:
            return adapter.handle_task(task)
        except (TypeError, ValueError):
            # 形参校验类错误：复用 handler 侧的 422 语义，包装为统一结构。
            raise
        except Exception:
            return {
                "task_id": task.get("id") or "unknown",
                "status": "failed",
                "output": None,
                "error": "Task processing failed internally.",
            }


class _ThreadingA2AServer(ThreadingMixIn, HTTPServer):
    """每连接一个线程：任务提交需要在等待中的 dispatch_and_wait（阻塞该请求的
    处理线程）之外，仍能并发接受下一个提交/轮询/取消请求，单线程 HTTPServer
    做不到这一点（同一时刻只能处理一个连接，会把所有其它请求都排队卡住）。
    """

    daemon_threads = True


def create_a2a_server(
    adapter: A2AInteropAdapter,
    port: int | None = None,
) -> tuple[HTTPServer, int]:
    """在 loopback 上创建并启动 A2A HTTP server。

    返回 (server, bound_port)。调用方负责 server.shutdown()。

    每个 server 实例拥有自己的 handler 子类与 _ServerContext，避免同一进程
    内多次调用时互相覆盖 adapter / 任务登记（修复跨 server 状态泄漏）。
    """
    actual_port = port or 0
    ctx = _ServerContext(adapter)
    # 动态子类：每个 server 独立的 RequestHandlerClass，绑定各自的 adapter 与 ctx，
    # 不再写共享基类 _A2AHTTPHandler 的类属性。
    handler_cls = type(
        "_A2AHTTPHandler",
        (_A2AHTTPHandler,),
        {"adapter": adapter, "ctx": ctx},
    )
    server = _ThreadingA2AServer(("127.0.0.1", actual_port), handler_cls)
    # 便于调用方访问生命周期接口（取消等）；不破坏 HTTPServer 的现有行为。
    server.a2a_context = ctx  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_port
