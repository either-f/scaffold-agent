"""agent-kernel CLI：一条命令驱动一次 run。

设计：对现有 AgentKernel + 适配器的薄封装，不是新执行引擎。
- `agent-kernel run "<prompt>"`：默认用 FakeScriptedModel 跑一轮（离线，无需 API key）；
  传 `--model <name>` 时改用 LiteLLMModel（需要 model extra，密钥走环境变量，
  复用 evals/run_worker_real.py 的 DEEPSEEK_API_KEY 等既有约定）。
- `agent-kernel resume <run_id> <checkpoint_label>`：从 --checkpoint-dir 读取 checkpoint 恢复。
  checkpoint_label 可为 "latest" 或 "turn_NNN_step_NNN"（与 fork.py 约定一致）。

退出码：成功 0；run.failed 或未处理异常 1；用户错误（参数缺失等）2。
错误一律打印到 stderr，不抛 traceback 给最终用户。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Sequence

from .adapters.model.fake import FakeScriptedModel
from .adapters.tools.local import default_toolbox
from .checkpoint import JsonCheckpointStore
from .kernel import AgentKernel
from .planners.react import ReactPlanner
from .types import RunState

# 默认离线脚本：与 examples/run_demo.py 同风格的固定 demo 响应，
# 让 `agent-kernel run` 在无 API key 时也能产出可观察的最终答案。
DEFAULT_SCRIPT: list[str] = [
    '{"thought": "用户问了一个简单问题，直接作答", "final": "4"}',
]

_CHECKPOINT_LABEL_RE = re.compile(r"turn_(\d+)_step_(\d+)")
DEFAULT_CHECKPOINT_DIR = ".agent-kernel/checkpoints"


class CliError(Exception):
    """用户可读的 CLI 错误：转换为退出码 1 并打印到 stderr，不抛 traceback。"""


def _build_fake_model() -> FakeScriptedModel:
    return FakeScriptedModel(list(DEFAULT_SCRIPT))


def _build_litellm_model(model_name: str):
    try:
        from .adapters.model.litellm import LiteLLMModel
    except ImportError as exc:
        raise CliError(
            f"--model {model_name!r} 需要 litellm，请先安装："
            "uv sync --extra model  或  pip install 'agent-kernel[model]'"
        ) from exc
    return LiteLLMModel(model_name)


def _load_checkpoint(store: JsonCheckpointStore, run_id: str, label: str) -> RunState:
    match = _CHECKPOINT_LABEL_RE.fullmatch(label)
    if match:
        state = store.load_step(run_id, int(match.group(1)), int(match.group(2)))
    elif label == "latest":
        state = store.load(run_id)
    else:
        raise CliError(
            f"checkpoint_label 必须是 'latest' 或 'turn_NNN_step_NNN'，收到: {label!r}"
        )
    if state is None:
        raise CliError(f"未找到 checkpoint: run_id={run_id!r} label={label!r}")
    return state


def _print_result(state: RunState) -> int:
    if state.status == "done":
        print(state.answer if state.answer is not None else "(done, no answer)")
        return 0
    if state.status == "paused":
        print(f"paused for approval (run_id={state.run_id}, step={state.step})")
        return 0
    # failed 或其它
    print(f"failed: {state.answer or 'run 未能完成'}", file=sys.stderr)
    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    model = (
        _build_litellm_model(args.model) if args.model else _build_fake_model()
    )
    store = JsonCheckpointStore(args.checkpoint_dir)
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=ReactPlanner(),
        checkpoints=store,
        max_steps=args.max_steps,
    )
    try:
        state = kernel.run(args.prompt)
    except Exception as exc:  # 打印而非抛出，用户友好
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    print(f"run_id={state.run_id} (status={state.status}, step={state.step})")
    return _print_result(state)


def _cmd_resume(args: argparse.Namespace) -> int:
    store = JsonCheckpointStore(args.checkpoint_dir)
    state = _load_checkpoint(store, args.run_id, args.checkpoint_label)

    model = (
        _build_litellm_model(args.model) if args.model else _build_fake_model()
    )
    # paused 状态恢复需要 approval；resume 默认自动批准，避免 CLI 卡在交互输入。
    approval = (lambda _call: True) if state.status == "paused" else None
    kernel = AgentKernel(
        model=model,
        tools=default_toolbox(),
        planner=ReactPlanner(),
        checkpoints=store,
        approval=approval,
        max_steps=args.max_steps,
    )
    try:
        state = kernel.resume(state)
    except Exception as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    return _print_result(state)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-kernel",
        description="agent-kernel CLI：驱动一次 run 或从 checkpoint 恢复。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="用 prompt 跑一轮（默认 FakeScriptedModel，离线）")
    p_run.add_argument("prompt", help="用户输入 prompt")
    p_run.add_argument(
        "--model", default=None,
        help="LiteLLM 模型名（如 deepseek/deepseek-chat）；需先安装 model extra 并设置 API key 环境变量",
    )
    p_run.add_argument(
        "--checkpoint-dir", default=DEFAULT_CHECKPOINT_DIR,
        help=f"checkpoint 落盘目录（默认 {DEFAULT_CHECKPOINT_DIR}）",
    )
    p_run.add_argument("--max-steps", type=int, default=10, help="最大步数（默认 10）")
    p_run.set_defaults(func=_cmd_run)

    p_resume = sub.add_parser("resume", help="从 checkpoint 恢复一个 run")
    p_resume.add_argument("run_id", help="要恢复的 run_id")
    p_resume.add_argument(
        "checkpoint_label",
        help="checkpoint 标签：'latest' 或 'turn_NNN_step_NNN'",
    )
    p_resume.add_argument(
        "--model", default=None,
        help="LiteLLM 模型名；不传则用 FakeScriptedModel",
    )
    p_resume.add_argument(
        "--checkpoint-dir", default=DEFAULT_CHECKPOINT_DIR,
        help=f"checkpoint 目录（默认 {DEFAULT_CHECKPOINT_DIR}）",
    )
    p_resume.add_argument("--max-steps", type=int, default=10, help="最大步数（默认 10）")
    p_resume.set_defaults(func=_cmd_resume)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except Exception as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
