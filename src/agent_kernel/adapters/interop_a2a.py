"""A2A 互操作适配器：Agent Card + 同步任务处理 + 最小 HTTP 传输层。

融合纪律：内核零第三方依赖；本模块仅使用标准库 http.server。
可选依赖 a2a-sdk 留到 pyproject.toml 的 [a2a] extra，CI 不要求。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from ..ports import InteropPort

MAX_REQUEST_BYTES = 1_048_576  # 1 MB


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

            try:
                result = self.adapter.handle_task(task)
            except (TypeError, ValueError) as e:
                self._send_error_json(422, str(e))
                return

            self._send_json(200, result)
        finally:
            if not body_consumed:
                self._drain_oversized_body()


def create_a2a_server(
    adapter: A2AInteropAdapter,
    port: int | None = None,
) -> tuple[HTTPServer, int]:
    """在 loopback 上创建并启动 A2A HTTP server。

    返回 (server, bound_port)。调用方负责 server.shutdown()。
    """
    actual_port = port or 0
    server = HTTPServer(("127.0.0.1", actual_port), _A2AHTTPHandler)
    server.RequestHandlerClass.adapter = adapter  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_port
