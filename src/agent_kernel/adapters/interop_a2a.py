"""A2A 互操作适配器：Agent Card + 异步任务生命周期 + 最小 HTTP 传输层。

仅使用标准库。任务历史保存在 adapter 实例内的有界内存注册表中，进程重启后丢失。
"""
from __future__ import annotations

import hmac
import inspect
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable, Literal
from urllib.parse import unquote, urlsplit

from ..ports import InteropPort

MAX_REQUEST_BYTES = 1_048_576  # 1 MB
MAX_TRACKED_TASKS = 1000
_ACTIVE_STATUSES = {"submitted", "working"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class TaskConflictError(RuntimeError):
    pass


class TaskRegistryFullError(RuntimeError):
    pass


class A2AInteropAdapter(InteropPort):
    """A2A 互操作适配器，含每实例独立的异步任务注册表。"""

    def __init__(
        self,
        task_handler: Callable[..., str],
        *,
        name: str = "agent-kernel",
        description: str = "通用 agent 内核（ReAct + 工具）",
        version: str = "0.1.0",
        url: str = "http://localhost:8080",
        provider: str = "agent-kernel",
        auth_token: str | None = None,
        max_tracked_tasks: int = MAX_TRACKED_TASKS,
    ) -> None:
        if auth_token is not None and not isinstance(auth_token, str):
            raise TypeError("auth_token 必须是 string 或 null")
        if max_tracked_tasks <= 0:
            raise ValueError("max_tracked_tasks 必须大于 0")
        self._task_handler = task_handler
        self._name = name
        self._description = description
        self._version = version
        self._url = url
        self._provider = provider
        self.auth_token = auth_token
        self._max_tracked_tasks = max_tracked_tasks
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._cancel_events: dict[str, threading.Event] = {}

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
                "taskPolling": True,
                "taskCancellation": True,
            },
            "skillDescriptions": [],
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
        }

    def handle_task(self, task: dict, cancel_event: threading.Event | None = None) -> dict:
        """同步执行单个任务；HTTP 路径通过 ``submit_task`` 在后台调用。"""
        task_id, user_input = self._validate_task(task)
        if cancel_event is not None and cancel_event.is_set():
            return _task_result(task_id, "cancelled")
        try:
            output = self._call_handler(user_input, cancel_event)
        except Exception:
            return _task_result(
                task_id,
                "failed",
                error="Task processing failed internally.",
            )
        if cancel_event is not None and cancel_event.is_set():
            return _task_result(task_id, "cancelled")
        return _task_result(task_id, "completed", output=output)

    # ---------------------------------------------------------------- lifecycle
    def submit_task(self, task: dict) -> dict:
        raw_id, _ = self._validate_task(task)
        task_id = raw_id if raw_id != "unknown" else secrets.token_urlsafe(16)
        normalized = dict(task)
        normalized["id"] = task_id

        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                if existing["status"] in _ACTIVE_STATUSES:
                    raise TaskConflictError(f"task already in flight: {task_id}")
                return dict(existing)
            self._make_room_locked()
            submitted = _task_result(task_id, "submitted")
            cancel_event = threading.Event()
            self._tasks[task_id] = submitted
            self._cancel_events[task_id] = cancel_event

        # 线程无法被强杀；取消事件会传给支持它的 handler，否则只丢弃最终结果。
        thread = threading.Thread(
            target=self._run_submitted,
            args=(normalized, cancel_event),
            daemon=True,
        )
        thread.start()
        return dict(submitted)

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            result = self._tasks.get(task_id)
            return dict(result) if result is not None else None

    def cancel_task(self, task_id: str) -> dict | None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return None
            if entry["status"] in _ACTIVE_STATUSES:
                event = self._cancel_events.get(task_id)
                if event is not None:
                    event.set()
                entry = _task_result(task_id, "cancelled")
                self._tasks[task_id] = entry
                self._cancel_events.pop(task_id, None)
            return dict(entry)

    def cancel(self, task_id: str) -> bool:
        """兼容 server.a2a_context.cancel(id)；终态或未知任务返回 False。"""
        before = self.get_task(task_id)
        if before is None or before["status"] not in _ACTIVE_STATUSES:
            return False
        result = self.cancel_task(task_id)
        return result is not None and result["status"] == "cancelled"

    def _run_submitted(
        self,
        task: dict,
        cancel_event: threading.Event,
    ) -> None:
        task_id = task["id"]
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None or current["status"] == "cancelled":
                return
            self._tasks[task_id] = _task_result(task_id, "working")

        result = self.handle_task(task, cancel_event)
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None or current["status"] == "cancelled":
                return  # 已取消：后台结果必须丢弃
            self._tasks[task_id] = result
            self._cancel_events.pop(task_id, None)

    def _call_handler(self, user_input: str, cancel_event: threading.Event | None) -> str:
        cancel_mode = _detect_cancel_mode(self._task_handler)
        if cancel_event is None or cancel_mode == "none":
            return self._task_handler(user_input)
        if cancel_mode == "positional":
            return self._task_handler(user_input, cancel_event)
        return self._task_handler(user_input, cancel_event=cancel_event)

    @staticmethod
    def _validate_task(task: dict) -> tuple[str, str]:
        if not isinstance(task, dict):
            raise TypeError("task 必须是 dict")
        raw_id = task.get("id")
        if raw_id is not None and not isinstance(raw_id, str):
            raise TypeError("task.id 必须是 string 或 null")
        user_input = task.get("input")
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("task.input 不能为空")
        return raw_id or "unknown", user_input

    def _make_room_locked(self) -> None:
        while len(self._tasks) >= self._max_tracked_tasks:
            oldest_terminal = next(
                (
                    task_id
                    for task_id, entry in self._tasks.items()
                    if entry["status"] in _TERMINAL_STATUSES
                ),
                None,
            )
            if oldest_terminal is None:
                raise TaskRegistryFullError("task registry is full")
            self._tasks.pop(oldest_terminal, None)
            self._cancel_events.pop(oldest_terminal, None)


def _detect_cancel_mode(handler: Callable[..., str]) -> Literal["none", "positional", "keyword"]:
    """Determine whether a handler accepts ``(input, cancel_event)`` without calling it."""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return "none"
    marker = threading.Event()
    try:
        signature.bind("input", marker)
    except TypeError:
        try:
            signature.bind("input", cancel_event=marker)
        except TypeError:
            return "none"
        return "keyword"
    return "positional"


def _task_result(
    task_id: str,
    status: str,
    *,
    output: object | None = None,
    error: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "output": output,
        "error": error,
    }


# -------------------------------------------------------------------- HTTP transport
class _A2AHTTPHandler(BaseHTTPRequestHandler):
    adapter: A2AInteropAdapter

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        pass

    def _send_json(self, code: int, body: dict | list, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _send_error_json(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})

    def _authorize(self) -> bool:
        token = self.adapter.auth_token
        if token is None:
            return True
        supplied = self.headers.get("Authorization", "")
        if hmac.compare_digest(
            supplied.encode("utf-8", errors="surrogatepass"),
            f"Bearer {token}".encode("utf-8", errors="surrogatepass"),
        ):
            return True
        self._send_json(
            401,
            {"error": "unauthorized"},
            {"WWW-Authenticate": "Bearer"},
        )
        return False

    def _task_id_from_path(self) -> str | None:
        path = urlsplit(self.path).path
        prefix = "/tasks/"
        if not path.startswith(prefix):
            return None
        task_id = unquote(path[len(prefix) :])
        return task_id if task_id and "/" not in task_id else None

    def _drain_oversized_body(
        self,
        max_bytes: int = MAX_REQUEST_BYTES + 1,
        timeout: float = 1.0,
    ) -> None:
        try:
            old_timeout = self.connection.gettimeout()
        except OSError:
            old_timeout = None
        try:
            self.connection.settimeout(timeout)
            drained = 0
            while drained < max_bytes:
                chunk = self.rfile.read(min(8192, max_bytes - drained))
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

    def do_GET(self) -> None:
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        if path == "/.well-known/agent-card.json":
            self._send_json(200, self.adapter.agent_card())
            return
        task_id = self._task_id_from_path()
        if task_id is None:
            self._send_error_json(404, "not found")
            return
        result = self.adapter.get_task(task_id)
        if result is None:
            self._send_error_json(404, "task not found")
            return
        self._send_json(200, result)

    def do_POST(self) -> None:
        body_consumed = False
        try:
            if not self._authorize():
                return
            if urlsplit(self.path).path != "/tasks":
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
            try:
                result = self.adapter.submit_task(task)
            except (TypeError, ValueError) as exc:
                self._send_error_json(422, str(exc))
                return
            except TaskConflictError as exc:
                self._send_error_json(409, str(exc))
                return
            except TaskRegistryFullError as exc:
                self._send_error_json(503, str(exc))
                return
            self._send_json(202 if result["status"] == "submitted" else 200, result)
        finally:
            if not body_consumed:
                self._drain_oversized_body()

    def do_DELETE(self) -> None:
        if not self._authorize():
            return
        task_id = self._task_id_from_path()
        if task_id is None:
            self._send_error_json(404, "not found")
            return
        before = self.adapter.get_task(task_id)
        if before is None:
            self._send_error_json(404, "task not found")
            return
        result = self.adapter.cancel_task(task_id)
        assert result is not None
        if before["status"] not in _ACTIVE_STATUSES:
            self._send_json(409, result)
            return
        self._send_json(200, result)


class _ThreadingA2AServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def create_a2a_server(
    adapter: A2AInteropAdapter,
    port: int | None = None,
) -> tuple[HTTPServer, int]:
    """在 loopback 上创建并启动 server；返回 ``(server, bound_port)``。"""
    handler_cls = type("_A2AHTTPHandler", (_A2AHTTPHandler,), {"adapter": adapter})
    server = _ThreadingA2AServer(("127.0.0.1", port or 0), handler_cls)
    server.a2a_context = adapter  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_port
