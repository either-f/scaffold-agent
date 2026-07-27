"""M0 离线演示；加 --m1 运行 DeepSeek + Filesystem/Fetch MCP。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_kernel.adapters.memory_sqlite import SqliteMemory
from agent_kernel.adapters.model_fake import FakeScriptedModel
from agent_kernel.adapters.model_litellm import LiteLLMModel
from agent_kernel.adapters.tools_local import default_toolbox
from agent_kernel.adapters.tools_mcp import McpToolbox, StdioServerConfig
from agent_kernel.checkpoint import JsonCheckpointStore
from agent_kernel.events import EventBus
from agent_kernel.kernel import AgentKernel
from agent_kernel.planners.react import ReactPlanner
from agent_kernel.skills.loader import DirSkillLoader

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


def event_bus() -> EventBus:
    bus = EventBus()
    bus.subscribe("*", lambda e: print(f"[event] {e.type:12s} {e.payload}"))
    return bus


def run_offline() -> None:
    kernel = AgentKernel(
        model=FakeScriptedModel(SCRIPT),
        tools=default_toolbox(),
        planner=ReactPlanner(),
        memory=SqliteMemory(),
        bus=event_bus(),
        checkpoints=JsonCheckpointStore("runs"),
    )
    print("可用技能:", [s.name for s in DirSkillLoader("skills_library").list_skills()])
    state = kernel.run("现在几点？顺便算一下 (3+4)*7")
    print(f"\n最终答案: {state.answer}\n状态: {state.status}, 步数: {state.step}")
    print(f"checkpoint 已写入 runs/{state.run_id}/")


def guard_example_fetch(args: dict) -> None:
    if args.get("url") not in {"https://example.com", "https://example.com/"}:
        raise PermissionError("M1 演示只允许 fetch https://example.com")


def run_m1() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    npm_cache = PROJECT_ROOT / ".cache" / "npm"
    filesystem_args = (
        ("/d", "/c", "npx", "--yes", "@modelcontextprotocol/server-filesystem@2026.7.10", str(PROJECT_ROOT))
        if os.name == "nt"
        else ("--yes", "@modelcontextprotocol/server-filesystem@2026.7.10", str(PROJECT_ROOT))
    )
    servers = {
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
    seen_tools: list[str] = []
    bus = event_bus()
    bus.subscribe("tool.before", lambda e: seen_tools.append(str(e.payload["tool"])))

    allow = FILESYSTEM_READ_TOOLS | {"fetch.fetch"}
    with McpToolbox(servers, allow=allow, guards={"fetch.fetch": guard_example_fetch}) as tools:
        kernel = AgentKernel(
            model=LiteLLMModel("deepseek/deepseek-chat"),
            tools=tools,
            planner=ReactPlanner(),
            bus=bus,
            max_steps=6,
        )
        readme = (PROJECT_ROOT / "README.md").as_posix()
        state = kernel.run(
            "必须按顺序完成："
            f"先调用 filesystem.read_text_file 读取 {readme}；"
            "再调用 fetch.fetch 获取 https://example.com，max_length=1000；"
            "最后用中文各用一句话概括两份内容。每轮只调用一个工具。"
        )

    required = {"filesystem.read_text_file", "fetch.fetch"}
    if state.status != "done" or not required.issubset(seen_tools):
        raise RuntimeError(f"M1 验收失败: status={state.status}, tools={seen_tools}")
    print(f"\nM1_OK 状态={state.status} 步数={state.step} 工具={seen_tools}")
    print(f"最终答案: {state.answer}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m1", action="store_true", help="运行真实 DeepSeek + MCP 演示")
    args = parser.parse_args()
    run_m1() if args.m1 else run_offline()


if __name__ == "__main__":
    main()
