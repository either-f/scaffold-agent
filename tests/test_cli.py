"""CLI 冒烟测试：run 默认路径 / resume / 失败退出码 / 公共 API 导出。

运行：PYTHONPATH=src python3 tests/test_cli.py   （也兼容 pytest）
"""
import os
import sys
import tempfile

sys.path.insert(0, "src")

import pytest

from agent_kernel import (  # noqa: F401  验证公共 API 可从顶层 import
    AgentKernel,
    Event,
    FinalAnswer,
    MemoryPort,
    Message,
    ModelPort,
    PlannerPort,
    RunState,
    ToolCall,
    ToolPort,
    ToolResult,
    ToolSpec,
    __version__,
)
from agent_kernel.cli import main
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.kernel import AgentKernel as _AK
from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.tools.local import default_toolbox
from agent_kernel.planners.react import ReactPlanner


def test_public_api_exports():
    # 顶层 import 可用，且不要求 import 内部子模块
    assert AgentKernel is _AK
    assert isinstance(__version__, str) and __version__
    for name in ["RunState", "Message", "ToolCall", "FinalAnswer",
                 "ToolResult", "ToolSpec", "Event",
                 "ModelPort", "ToolPort", "MemoryPort", "PlannerPort"]:
        assert name in sys.modules["agent_kernel"].__dict__


def test_run_default_exits_zero_and_prints_answer(capsys):
    rc = main(["run", "2+2 是多少"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4" in out


def test_run_writes_checkpoint(capsys, tmp_path):
    rc = main(["run", "2+2", "--checkpoint-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # 提取 run_id
    assert "run_id=" in out
    run_id = out.split("run_id=")[1].split()[0]
    store = JsonCheckpointStore(str(tmp_path))
    loaded = store.load(run_id)
    assert loaded is not None and loaded.status == "done"


def test_resume_latest_after_run(capsys, tmp_path):
    rc = main(["run", "2+2", "--checkpoint-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    run_id = out.split("run_id=")[1].split()[0]

    rc2 = main(["resume", run_id, "latest", "--checkpoint-dir", str(tmp_path)])
    out2 = capsys.readouterr().out
    # done 状态的 run 不能 resume（kernel.resume 拒绝终态）-> failed: ...
    assert rc2 == 1


def _make_paused_checkpoint(tmp_path) -> str:
    """用一个会停在 paused 的 run 造 checkpoint，供 resume 测试。"""
    script = ['{"thought": "t", "tool": "calc", "args": {"expression": "1+1"}}']
    store = JsonCheckpointStore(str(tmp_path))
    crashing_kernel = AgentKernel(
        model=FakeScriptedModel(list(script)),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        checkpoints=store,
        approval=lambda _c: (_ for _ in ()).throw(SystemExit("crash")),
    )
    with pytest.raises(SystemExit):
        crashing_kernel.run("算 1+1", state=RunState(run_id="cli-resume-1"))
    return "cli-resume-1"


def test_resume_paused_checkpoint_label(capsys, tmp_path):
    run_id = _make_paused_checkpoint(tmp_path)
    store = JsonCheckpointStore(str(tmp_path))
    loaded = store.load(run_id)
    assert loaded is not None and loaded.status == "paused"
    label = f"turn_{loaded.turn:03d}_step_{loaded.step:03d}"

    rc = main(["resume", run_id, label, "--checkpoint-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # resume 后默认脚本会给出 final "4"
    assert "4" in out


def test_resume_missing_checkpoint_fails(capsys, tmp_path):
    rc = main(["resume", "no-such-run", "latest", "--checkpoint-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed" in err


def test_resume_bad_label_fails(capsys, tmp_path):
    rc = main(["resume", "x", "not-a-label", "--checkpoint-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc != 0
    assert "failed" in err


def test_no_subcommand_errors(capsys):
    with pytest.raises(SystemExit):
        main([])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
