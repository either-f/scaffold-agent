"""M4: 渐进式技能工具 + Docker 沙箱 + CodeAct 策略测试。

SkillToolbox：元信息-only 披露、正文按需加载、普通工具委托、动态文件夹发现、未知名拒绝。
DockerSandbox：安全 flag 命令构造、空代码/超时/非零退出、SandboxToolbox 契约。
CodeActPlanner：端到端 fake-model 走通"写代码→沙箱→final"。
可选：检测到 Docker 时做真实隔离探针（不挂载宿主路径）。
运行：PYTHONPATH=src python3 tests/test_m4.py   （也兼容 pytest）
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.sandbox_docker import (
    PYTHON_SNIPPET,
    DockerSandbox,
    SandboxError,
    SandboxLimits,
    SandboxToolbox,
)
from agent_kernel.adapters.tools_local import LocalToolbox
from agent_kernel.adapters.tools_skills import SkillToolbox
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.codeact import CodeActPlanner
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader

_SKILL_MD = """---
name: {name}
description: {desc}
---

{body}
"""

BODY_SECRET = "STEP-BY-STEP-SECRET-BODY"


def _make_skills_root(tmp: str, name: str = "web-research") -> Path:
    root = Path(tmp) / "skills_library"
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        _SKILL_MD.format(name=name, desc="联网调研", body=BODY_SECRET),
        encoding="utf-8",
    )
    return root


def _make_toolbox(tmp: str):
    inner = LocalToolbox()
    inner.register("calc", "计算器", lambda expression: f"calc:{expression}")
    loader = DirSkillLoader(str(_make_skills_root(tmp)))
    return SkillToolbox(inner, loader)


def test_metadata_only_disclosure():
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_toolbox(tmp)
        specs = box.list_tools()
        names = [s.name for s in specs]
        assert "calc" in names, "被包装工具必须保留"
        load_specs = [s for s in specs if s.name != "calc"]
        assert len(load_specs) == 1, "有且仅有一个命名空间 load 工具"
        load_spec = load_specs[0]
        # 元信息出现在描述/schema 中
        assert "web-research" in load_spec.description
        assert "联网调研" in load_spec.description
        # 正文绝不出现在发现输出里
        for s in specs:
            assert BODY_SECRET not in s.description
            assert BODY_SECRET not in str(s.parameters)


def test_on_demand_body_loading():
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_toolbox(tmp)
        load_name = [s.name for s in box.list_tools() if s.name != "calc"][0]
        body = box.call(load_name, {"name": "web-research"})
        assert BODY_SECRET in body, "load 调用应返回正文"
        assert "name: web-research" not in body, "frontmatter 不应混入正文"


def test_delegates_ordinary_tools():
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_toolbox(tmp)
        assert box.call("calc", {"expression": "1+1"}) == "calc:1+1"


def test_dynamic_folder_addition():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_skills_root(tmp)
        loader = DirSkillLoader(str(root))
        inner = LocalToolbox()
        box = SkillToolbox(inner, loader)
        before = box.list_tools()[-1].description
        assert "code-review" not in before
        # 新增技能文件夹，零代码变更
        d = root / "code-review"
        d.mkdir()
        (d / "SKILL.md").write_text(
            _SKILL_MD.format(name="code-review", desc="代码评审", body="CR-BODY"),
            encoding="utf-8",
        )
        after = box.list_tools()[-1].description
        assert "code-review" in after and "代码评审" in after
        load_name = [s.name for s in box.list_tools()][-1]
        assert "CR-BODY" in box.call(load_name, {"name": "code-review"})


def test_unknown_and_traversal_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_toolbox(tmp)
        load_name = [s.name for s in box.list_tools() if s.name != "calc"][0]
        for bad in ("nonexistent", "../etc", "..", "web-research/../../x", ""):
            try:
                box.call(load_name, {"name": bad})
                raise AssertionError(f"应拒绝: {bad!r}")
            except KeyError:
                pass
        try:
            box.call("no-such-tool", {})
            raise AssertionError("未知普通工具应 fail closed")
        except KeyError:
            pass


# ----------------------------------------------------------- M4B: Docker 沙箱

SECRET_CODE = "import os  # AK-SECRET-CODE-MARKER"


def _recording_runner(calls, returncode=0, stdout="", stderr=""):
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


def _assert_locked_down(cmd: list[str], sandbox: DockerSandbox) -> None:
    assert cmd[:2] == ["docker", "run"], "必须走 docker run"
    assert "--rm" in cmd and "-i" in cmd
    joined = " ".join(cmd)
    # 网络与提权：构造上禁用
    assert "--network none" in joined
    assert "--read-only" in cmd
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    # 资源限额
    assert f"--pids-limit {sandbox.limits.pids}" in joined
    assert f"--cpus {sandbox.limits.cpus}" in joined
    assert f"--memory {sandbox.limits.memory}" in joined
    # 唯一可写处：tmpfs，且 noexec+nosuid
    tmpfs = cmd[cmd.index("--tmpfs") + 1]
    assert tmpfs.startswith("/tmp:") and "noexec" in tmpfs and "nosuid" in tmpfs
    # 宿主路径永不挂载
    assert "-v" not in cmd and "--volume" not in cmd and "--mount" not in cmd
    assert cmd[cmd.index("-e") + 1] == "_AKC", "只允许白名单传入代码环境变量"
    # 入口固定为 python -c <snippet>
    assert cmd[-3:-1] == ["python", "-c"] and cmd[-1] == PYTHON_SNIPPET


def test_sandbox_command_is_locked_down():
    sandbox = DockerSandbox(
        image="python:3.11.14-slim-bookworm", timeout=5, limits=SandboxLimits(pids=64, cpus="0.5", memory="256m")
    )
    cmd = sandbox.build_command()
    _assert_locked_down(cmd, sandbox)
    assert "python:3.11.14-slim-bookworm" in cmd


def test_sandbox_rejects_empty_code():
    sandbox = DockerSandbox(runner=_recording_runner([]))
    for bad in ("", "   \n\t  ", None, 42):
        try:
            sandbox.execute(bad)  # type: ignore[arg-type]
            raise AssertionError(f"空代码应被拒绝: {bad!r}")
        except ValueError:
            pass
    try:
        DockerSandbox(timeout=0)
        raise AssertionError("非正超时应被拒绝")
    except ValueError:
        pass


def test_sandbox_runner_invocation_and_timeout():
    calls: list = []
    sandbox = DockerSandbox(timeout=7, runner=_recording_runner(calls, stdout="49\n"))
    assert sandbox.execute(SECRET_CODE + "\nprint(49)") == "49"
    cmd, kwargs = calls[0]
    _assert_locked_down(cmd, sandbox)
    assert kwargs["timeout"] == 7, "可配置超时必须传给 runner"
    assert kwargs["input"] == "", "stdin 必须显式置空，防容器挂起"
    assert kwargs["capture_output"] and kwargs["text"]
    # 代码只经 base64 环境变量进入容器：命令行与 env 中均不出现明文
    assert SECRET_CODE not in " ".join(cmd)
    assert SECRET_CODE not in str(kwargs["env"])
    import base64

    base64.b64decode(kwargs["env"]["_AKC"]).decode()  # env 值是合法 base64

    def timeout_runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    try:
        DockerSandbox(timeout=3, runner=timeout_runner).execute(SECRET_CODE + "\nwhile True: pass")
        raise AssertionError("超时必须显式抛出")
    except SandboxError as e:
        assert "超时" in str(e) and "3" in str(e)
        assert SECRET_CODE not in str(e), "报错不得泄露代码/秘密"


def test_sandbox_nonzero_exit_and_output_truncation():
    stderr = "Traceback (most recent call last):\nZeroDivisionError: division by zero"
    sandbox = DockerSandbox(runner=_recording_runner([], returncode=1, stderr=stderr))
    try:
        sandbox.execute("1/0")
        raise AssertionError("非零退出必须显式抛出")
    except SandboxError as e:
        assert "exit 1" in str(e) and "ZeroDivisionError" in str(e)

    huge = DockerSandbox(runner=_recording_runner([], stdout="x" * 5000))
    out = huge.execute("print('x'*5000)")
    assert len(out) <= 4096 + 16 and out.endswith("[截断]")


def test_sandbox_toolbox_contract():
    box = SandboxToolbox(DockerSandbox(runner=_recording_runner([], stdout="hi")))
    specs = box.list_tools()
    assert len(specs) == 1 and specs[0].name == "python_execute"
    assert "code" in specs[0].parameters["properties"]
    assert box.call("python_execute", {"code": "print('hi')"}) == "hi"
    try:
        box.call("bash_execute", {"code": "rm -rf /"})
        raise AssertionError("沙箱工具箱只允许 python_execute 一个工具")
    except KeyError:
        pass
    try:
        box.call("python_execute", {"code": "   "})
        raise AssertionError("空代码应经由工具调用同样被拒绝")
    except ValueError:
        pass


def test_codeact_end_to_end_with_fake_model():
    """fake-model 两轮：先写代码调沙箱，再据 stdout 给 final；全程走真实内核。"""
    calls: list = []
    script = [
        '{"thought": "需要算 (3+4)*7", "tool": "python_execute", "args": {"code": "print((3+4)*7)"}}',
        '{"thought": "沙箱返回 49", "final": "结果是 49"}',
    ]
    sandbox = DockerSandbox(runner=_recording_runner(calls, stdout="49\n"))
    kernel = AgentKernel(
        model=FakeScriptedModel(list(script)),
        tools=SandboxToolbox(sandbox),
        planner=CodeActPlanner(),
    )
    state = kernel.run("算一下 (3+4)*7")
    assert state.status == "done" and "49" in (state.answer or "")
    assert len(calls) == 1, "代码必须且只能进沙箱执行一次"
    _assert_locked_down(calls[0][0], sandbox)
    assert any(m.role == "tool" and m.content == "49" for m in state.messages)


def test_codeact_prompt_and_react_stays_default():
    """CodeAct 只换指令模板；ReAct 原 prompt 保持不变。"""
    assert CodeActPlanner.system_template != ReactPlanner.system_template
    assert "python_execute" in CodeActPlanner.system_template
    assert issubclass(CodeActPlanner, ReactPlanner)

    class RecordingModel(FakeScriptedModel):
        def complete(self, messages, tools):
            self.messages = messages
            return super().complete(messages, tools)

    model = RecordingModel(['{"thought": "t", "final": "ok"}'])
    CodeActPlanner().step(
        __import__("agent_kernel.types", fromlist=["RunState"]).RunState(),
        model,
        SandboxToolbox(DockerSandbox(runner=_recording_runner([]))),
        None,
    )
    assert "python_execute" in model.messages[0].content, "CodeAct 指令必须注入 system prompt"


def test_docker_isolation_probe():
    """可选探针：真 Docker 在场时，证明容器内读不到任意宿主路径。"""
    if not shutil.which("docker"):
        print("SKIP: 未检测到 docker，跳过隔离探针")
        return
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15, check=True
        )
    except Exception:
        print("SKIP: docker daemon 不可用，跳过隔离探针")
        return
    out = DockerSandbox(image="python:3.11.14-slim-bookworm", timeout=120).execute(
        "import os\n"
        "probe = '/proc/1/root/etc/hostname'\n"
        "print('net', os.path.exists('/sys/class/net/eth0'))\n"
        "try:\n"
        "    open(probe)\n"
        "    print('escape', True)\n"
        "except OSError:\n"
        "    print('escape', False)\n"
    )
    assert "escape False" in out, "容器绝不应读到宿主路径"
    assert "net False" in out, "--network none 下不应存在 eth0"
    print("OK: docker 隔离探针通过（无挂载、无网络）")


if __name__ == "__main__":
    test_metadata_only_disclosure()
    test_on_demand_body_loading()
    test_delegates_ordinary_tools()
    test_dynamic_folder_addition()
    test_unknown_and_traversal_rejected()
    test_sandbox_command_is_locked_down()
    test_sandbox_rejects_empty_code()
    test_sandbox_runner_invocation_and_timeout()
    test_sandbox_nonzero_exit_and_output_truncation()
    test_sandbox_toolbox_contract()
    test_codeact_end_to_end_with_fake_model()
    test_codeact_prompt_and_react_stays_default()
    test_docker_isolation_probe()
    print("OK: M4 全部测试通过")
