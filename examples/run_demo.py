"""M0 离线演示；加 --m1 运行 DeepSeek + Filesystem/Fetch MCP。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory.sqlite import SqliteMemory
from agent_kernel.adapters.model.fake import FakeScriptedModel
from agent_kernel.adapters.model.litellm import LiteLLMModel
from agent_kernel.adapters.tools.local import default_toolbox
from agent_kernel.adapters.tools.mcp import McpToolbox, StdioServerConfig
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader
from agent_kernel.types import RunState, ToolCall

SCRIPT = [
    '{"thought": "先获取当前时间", "tool": "now", "args": {}}',
    '{"thought": "再计算表达式", "tool": "calc", "args": {"expression": "(3+4)*7"}}',
    '{"thought": "信息齐了", "final": "现在时间见上一步工具结果；(3+4)*7 = 49。"}',
]

FILESYSTEM_READ_TOOLS = {
    "filesystem.read_text_file",
    "filesystem.read_multiple_files",
    "filesystem.list_directory",
    "filesystem.list_directory_with_sizes",
    "filesystem.directory_tree",
    "filesystem.search_files",
    "filesystem.get_file_info",
    "filesystem.list_allowed_directories",
}

M1_PROMPT = (
    "必须按顺序完成：先调用 filesystem.read_text_file 读取 {readme}；"
    "再调用 fetch.fetch 获取 https://example.com，max_length=1000；"
    "最后用中文各用一句话概括两份内容。每轮只调用一个工具。"
)


def event_bus() -> EventBus:
    bus = EventBus()
    bus.subscribe("*", lambda e: print(f"[event] {e.type:12s} {e.payload}"))
    return bus


def cli_approval(call: ToolCall) -> bool:
    print(f"\n待审批工具: {call.name}\n参数: {json.dumps(call.args, ensure_ascii=False)}")
    try:
        answer = input("允许执行？[y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def run_offline(hitl: bool = False) -> None:
    kernel = AgentKernel(
        model=FakeScriptedModel(SCRIPT),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        memory=SqliteMemory(),
        bus=event_bus(),
        checkpoints=JsonCheckpointStore("runs"),
        approval=cli_approval if hitl else None,
    )
    print("可用技能:", [s.name for s in DirSkillLoader("skills_library").list_skills()])
    state = kernel.run("现在几点？顺便算一下 (3+4)*7")
    print(f"\n最终答案: {state.answer}\n状态: {state.status}, 步数: {state.step}")
    print(f"checkpoint 已写入 runs/{state.run_id}/")


def guard_example_fetch(args: dict) -> None:
    if args.get("url") not in {"https://example.com", "https://example.com/"}:
        raise PermissionError("M1 演示只允许 fetch https://example.com")


def m1_servers() -> dict[str, StdioServerConfig]:
    npm_cache = PROJECT_ROOT / ".cache" / "npm"
    filesystem_args = (
        ("/d", "/c", "npx", "--yes", "@modelcontextprotocol/server-filesystem@2026.7.10", str(PROJECT_ROOT))
        if os.name == "nt"
        else ("--yes", "@modelcontextprotocol/server-filesystem@2026.7.10", str(PROJECT_ROOT))
    )
    return {
        "filesystem": StdioServerConfig(
            command=os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else "npx",
            args=filesystem_args,
            env={"npm_config_cache": str(npm_cache)},
        ),
        "fetch": StdioServerConfig(
            command=sys.executable,
            args=("-m", "mcp_server_fetch"),
            env={"PYTHONIOENCODING": "utf-8"},
        ),
    }


def successful_tools(state: RunState) -> list[str]:
    return [
        str(message.name)
        for message in state.messages
        if message.role == "tool"
        and message.name
        and not message.content.startswith(("[tool-error]", "[HITL]"))
    ]


def run_m1(resume_id: str | None = None, hitl: bool = False) -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    store = JsonCheckpointStore("runs")
    state = store.load(resume_id) if resume_id else RunState()
    if state is None:
        raise RuntimeError(f"找不到 checkpoint: {resume_id}")
    if resume_id and state.status == "paused" and not hitl:
        raise RuntimeError("恢复待审批 checkpoint 必须同时传 --hitl")

    bus = event_bus()
    allow = FILESYSTEM_READ_TOOLS | {"fetch.fetch"}
    with McpToolbox(
        m1_servers(),
        allow=allow,
        guards={"fetch.fetch": guard_example_fetch},
        request_timeout_seconds=30,
        retryable=allow,
        max_retries=1,
    ) as tools:
        kernel = AgentKernel(
            model=LiteLLMModel("deepseek/deepseek-chat", timeout=60, num_retries=2, temperature=0),
            tools=tools,
            planner=ReactPlanner(),
            bus=bus,
            checkpoints=store,
            approval=cli_approval if hitl else None,
            max_steps=6,
        )
        try:
            state = (
                kernel.resume(state)
                if resume_id
                else kernel.run(M1_PROMPT.format(readme=(PROJECT_ROOT / "README.md").as_posix()), state)
            )
        except KeyboardInterrupt:
            print(f"\n运行已中断。恢复命令: python examples/run_demo.py --m1 --resume {state.run_id} --hitl")
            raise

    required = {"filesystem.read_text_file", "fetch.fetch"}
    called = successful_tools(state)
    if state.status != "done" or not required.issubset(called):
        raise RuntimeError(f"M1 验收失败: status={state.status}, tools={called}")
    print(f"\nM1_OK 状态={state.status} 步数={state.step} 工具={called}")
    print(f"最终答案: {state.answer}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m1", action="store_true", help="运行真实 DeepSeek + MCP 演示")
    parser.add_argument("--hitl", action="store_true", help="每次工具调用前进行 CLI 审批")
    parser.add_argument("--resume", metavar="RUN_ID", help="恢复 M1 checkpoint")
    args = parser.parse_args()
    if args.resume and not args.m1:
        parser.error("--resume 必须与 --m1 一起使用")
    try:
        run_m1(args.resume, args.hitl) if args.m1 else run_offline(args.hitl)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
