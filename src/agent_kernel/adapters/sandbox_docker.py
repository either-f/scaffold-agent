"""Docker 沙箱：在锁死的容器里执行模型生成的 Python 代码。

安全基线（全部通过命令构造保证，与宿主环境解耦）：
  - 无宿主挂载；根文件系统只读；唯一可写处是 /tmp tmpfs（noexec/nosuid）。
  - --network none：容器内零网络。
  - 丢弃全部 capabilities + no-new-privileges：禁止提权。
  - pids / cpu / memory 限额：防 fork 炸弹与资源耗尽。
  - 代码经 stdin 以 base64 传入 `python -c`，避免 -i 管道语义差异。

runner 可注入，默认 subprocess；单测用 fake runner 断言命令构造，无需 Docker。
"""
from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from ..ports import ToolPort
from ..types import ToolSpec

DEFAULT_IMAGE = "python:3.11.14-slim-bookworm"
MAX_CAPTURE_CHARS = 4096
PYTHON_SNIPPET = (
    "import base64,os;"
    "exec(compile(base64.b64decode(os.environ['_AKC']),'sandbox.py','exec'),{'__name__':'__main__'})"
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass
class SandboxLimits:
    """容器资源限额。对应 docker run 的 --pids-limit / --cpus / --memory。"""

    pids: int = 128
    cpus: str = "1"
    memory: str = "512m"


class SandboxError(Exception):
    """沙箱执行失败：超时或非零退出。消息不含任何输入代码，避免泄露敏感内容。"""


class DockerSandbox:
    """通过 `docker run --rm -i` 启动一次性容器执行 Python 代码。"""

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        timeout: float = 30.0,
        limits: SandboxLimits | None = None,
        runner: Runner | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("沙箱超时必须为正数秒")
        self.image = image
        self.timeout = float(timeout)
        self.limits = limits or SandboxLimits()
        self._runner: Runner = runner or subprocess.run

    def build_command(self) -> list[str]:
        """构造锁死的 docker run 命令（与执行分离，便于审计与单测）。"""
        return [
            "docker", "run", "--rm", "-i",
            "--network", "none",                      # 无网络
            "--read-only",                            # 根文件系统只读
            "--cap-drop", "ALL",                      # 丢弃全部 capabilities
            "--security-opt", "no-new-privileges",    # 禁止提权
            "--pids-limit", str(self.limits.pids),
            "--cpus", str(self.limits.cpus),
            "--memory", self.limits.memory,
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # 唯一可写处
            "-e", "_AKC",                             # 仅白名单传入代码环境变量
            self.image,
            "python", "-c", PYTHON_SNIPPET,
        ]

    def execute(self, code: str) -> str:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("沙箱拒绝执行空代码")
        env = {"_AKC": base64.b64encode(code.encode("utf-8")).decode("ascii")}
        try:
            proc = self._runner(
                self.build_command(),
                input="",  # 显式空 stdin：占位 -i，防止容器等待输入
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise SandboxError(f"沙箱执行超时（>{self.timeout:g}s），容器已被销毁") from None
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()
            detail = tail[-1][:200] if tail else "无 stderr 输出"
            raise SandboxError(f"沙箱执行失败（exit {proc.returncode}）：{detail}")
        out = (proc.stdout or "").rstrip("\n")
        if len(out) > MAX_CAPTURE_CHARS:
            out = out[:MAX_CAPTURE_CHARS] + "…[截断]"
        return out


class SandboxToolbox(ToolPort):
    """单工具工具箱：把 DockerSandbox 暴露为 python_execute 工具。"""

    TOOL_NAME = "python_execute"
    _SPEC = ToolSpec(
        name=TOOL_NAME,
        description=(
            "在隔离的 Docker 沙箱中执行 Python 3 代码并返回 stdout。"
            "沙箱无网络、无宿主文件挂载、根文件系统只读；用 print() 输出结果。"
        ),
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
            "required": ["code"],
        },
    )

    def __init__(self, sandbox: DockerSandbox | None = None) -> None:
        self.sandbox = sandbox or DockerSandbox()

    def list_tools(self) -> list[ToolSpec]:
        return [self._SPEC]

    def call(self, name: str, args: dict) -> str:
        if name != self.TOOL_NAME:
            raise KeyError(f"未知工具: {name}")
        return self.sandbox.execute(str(args.get("code", "")))
