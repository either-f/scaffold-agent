"""DaytonaSandbox 单测：假 client_factory 离线验证控制流，不需要真实 Daytona 账号。

运行：PYTHONPATH=src python3 tests/test_sandbox_daytona.py   （也兼容 pytest）
"""
import sys

import pytest

sys.path.insert(0, "src")

daytona = pytest.importorskip("daytona")
DaytonaError = daytona.DaytonaError

from agent_kernel.adapters.sandbox_daytona import DaytonaSandbox
from agent_kernel.adapters.sandbox_docker import MAX_CAPTURE_CHARS, SandboxError


class FakeResponse:
    def __init__(self, result: str, exit_code: int = 0) -> None:
        self.result = result
        self.exit_code = exit_code


class FakeProcess:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, float | None]] = []

    def code_run(self, code: str, timeout: float | None = None):
        self.calls.append((code, timeout))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeSandbox:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.process = FakeProcess(response)
        self.deleted = False
        self.delete_raises = False

    def delete(self):
        self.deleted = True
        if self.delete_raises:
            raise DaytonaError("delete failed")


class FakeClient:
    def __init__(self, sandbox: FakeSandbox | Exception) -> None:
        self._sandbox = sandbox
        self.create_params = None

    def create(self, params):
        self.create_params = params
        if isinstance(self._sandbox, Exception):
            raise self._sandbox
        return self._sandbox


def _sandbox_with(response, client_factory_holder=None):
    fake_sandbox = FakeSandbox(response)
    client = FakeClient(fake_sandbox)
    sandbox = DaytonaSandbox(client_factory=lambda: client)
    if client_factory_holder is not None:
        client_factory_holder["client"] = client
        client_factory_holder["sandbox"] = fake_sandbox
    return sandbox


def test_normal_execution_returns_stdout():
    sandbox = _sandbox_with(FakeResponse("4\n"))
    assert sandbox.execute("print(2+2)") == "4"


def test_create_uses_ephemeral_params():
    holder: dict = {}
    _sandbox_with(FakeResponse("ok"), holder).execute("print(1)")
    params = holder["client"].create_params
    assert params.language == "python"
    assert params.auto_delete_interval == 0


def test_sandbox_deleted_after_success():
    holder: dict = {}
    _sandbox_with(FakeResponse("ok"), holder).execute("print(1)")
    assert holder["sandbox"].deleted is True


def test_nonzero_exit_raises_sandbox_error():
    sandbox = _sandbox_with(FakeResponse("Traceback...\nValueError: boom", exit_code=1))
    try:
        sandbox.execute("raise ValueError('boom')")
        assert False, "应该抛出 SandboxError"
    except SandboxError as exc:
        assert "boom" in str(exc)


def test_sandbox_deleted_even_on_nonzero_exit():
    holder: dict = {}
    sandbox = _sandbox_with(FakeResponse("err", exit_code=1), holder)
    try:
        sandbox.execute("bad code")
    except SandboxError:
        pass
    assert holder["sandbox"].deleted is True


def test_code_run_exception_maps_to_sandbox_error():
    sandbox = _sandbox_with(DaytonaError("network blip"))
    try:
        sandbox.execute("print(1)")
        assert False, "应该抛出 SandboxError"
    except SandboxError:
        pass


def test_create_exception_maps_to_sandbox_error():
    client = FakeClient(DaytonaError("quota exceeded"))
    sandbox = DaytonaSandbox(client_factory=lambda: client)
    try:
        sandbox.execute("print(1)")
        assert False, "应该抛出 SandboxError"
    except SandboxError:
        pass


def test_output_truncated_past_max_capture_chars():
    sandbox = _sandbox_with(FakeResponse("x" * (MAX_CAPTURE_CHARS + 500)))
    out = sandbox.execute("print('x'*...)")
    assert len(out) <= MAX_CAPTURE_CHARS + len("…[截断]")
    assert out.endswith("…[截断]")


def test_empty_code_rejected_without_touching_client():
    sandbox = _sandbox_with(FakeResponse("unused"))
    try:
        sandbox.execute("   ")
        assert False, "应该拒绝空代码"
    except ValueError:
        pass


def test_missing_api_key_raises_value_error(monkeypatch):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    try:
        DaytonaSandbox()
        assert False, "应该要求 DAYTONA_API_KEY"
    except ValueError:
        pass


def test_delete_failure_does_not_mask_result():
    fake_sandbox = FakeSandbox(FakeResponse("4\n"))
    fake_sandbox.delete_raises = True
    client = FakeClient(fake_sandbox)
    sandbox = DaytonaSandbox(client_factory=lambda: client)
    assert sandbox.execute("print(2+2)") == "4"


if __name__ == "__main__":
    test_normal_execution_returns_stdout()
    test_create_uses_ephemeral_params()
    test_sandbox_deleted_after_success()
    test_nonzero_exit_raises_sandbox_error()
    test_sandbox_deleted_even_on_nonzero_exit()
    test_code_run_exception_maps_to_sandbox_error()
    test_create_exception_maps_to_sandbox_error()
    test_output_truncated_past_max_capture_chars()
    test_empty_code_rejected_without_touching_client()
    test_delete_failure_does_not_mask_result()
    print("OK: DaytonaSandbox 测试全部通过（missing_api_key 测试需 pytest 的 monkeypatch）")
