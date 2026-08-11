"""Daytona 云沙箱：跟 DockerSandbox 同签名（execute(code) -> str），验证沙箱后端可否
零改动替换——`SandboxToolbox` 不关心拿到的是本地容器还是远程沙箱。

隔离边界跟 DockerSandbox 不同：DockerSandbox 本地起容器、显式声明 --network none /
--cap-drop ALL / cgroup 限额；Daytona 是托管服务，网络出站策略、超售策略、内核隔离
方式由 Daytona 平台承担，本 adapter 不重复声明这些参数。见 ADR-0012 的边界对比。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from .sandbox_docker import MAX_CAPTURE_CHARS, SandboxError

ClientFactory = Callable[[], Any]


class DaytonaSandbox:
    """每次 execute() 创建一个 ephemeral 沙箱、跑一次代码、删除，跟 DockerSandbox
    `docker run --rm` 的一次性生命周期语义对齐——调用之间不残留状态。

    `client_factory` 可注入（对齐 `DockerSandbox` 的 `runner` 注入点），默认真实构造
    `daytona.Daytona`；单测传一个假 client 工厂即可离线验证控制流（错误映射、截断、
    ephemeral 参数、异常时仍尝试删除），无需真实 Daytona 账号——跟"是否验证过真实
    Daytona 基础设施保证"是两件事，见 ADR-0012 坦诚声明。
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        language: str = "python",
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("沙箱超时必须为正数秒")
        self.timeout = float(timeout)
        self.language = language
        if client_factory is not None:
            self._client_factory = client_factory
        else:
            try:
                from daytona import Daytona, DaytonaConfig
            except ImportError as exc:
                raise ImportError("需要 daytona 依赖：uv sync --extra daytona") from exc
            key = api_key or os.environ.get("DAYTONA_API_KEY")
            if not key:
                raise ValueError("需要 DAYTONA_API_KEY")
            self._client_factory = lambda: Daytona(DaytonaConfig(api_key=key))

    def execute(self, code: str) -> str:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("沙箱拒绝执行空代码")

        from daytona import CreateSandboxFromSnapshotParams
        from daytona import DaytonaError as _DaytonaError

        client = self._client_factory()
        try:
            sandbox = client.create(
                CreateSandboxFromSnapshotParams(language=self.language, auto_delete_interval=0)
            )
        except _DaytonaError as exc:
            raise SandboxError(f"沙箱创建失败：{exc}") from None

        try:
            try:
                response = sandbox.process.code_run(code, timeout=self.timeout)
            except _DaytonaError as exc:
                raise SandboxError(f"沙箱执行失败：{exc}") from None
            if response.exit_code != 0:
                tail = (response.result or "").strip().splitlines()
                detail = tail[-1][:200] if tail else "无输出"
                raise SandboxError(f"沙箱执行失败（exit {response.exit_code}）：{detail}")
            out = (response.result or "").rstrip("\n")
            if len(out) > MAX_CAPTURE_CHARS:
                out = out[:MAX_CAPTURE_CHARS] + "…[截断]"
            return out
        finally:
            # ponytail: auto_delete_interval=0 已经是"停止即删"兜底，这里主动删除只为
            # 尽快释放、减少计费窗口；删除失败不影响本次已拿到的执行结果。
            try:
                sandbox.delete()
            except _DaytonaError:
                pass
