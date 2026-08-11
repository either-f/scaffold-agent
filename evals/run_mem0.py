"""M8 真实 mem0 eval：真实 DeepSeek+DashScope+本地 Chroma 验证 Mem0Memory add→search 往返。"""
from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_mem0() -> dict:
    from agent_kernel.adapters.memory.mem0_adapter import Mem0Memory

    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY")

    persist_dir = tempfile.mkdtemp(prefix="mem0-eval-")
    try:
        memory = Mem0Memory(namespace="eval-user", persist_dir=persist_dir)
        memory.add("run-1", "user", "我叫 Alex，最喜欢的编程语言是 Rust。")
        memory.add("run-1", "assistant", "记住了，Alex 喜欢 Rust。")
        results = memory.search("用户最喜欢什么编程语言？", k=5)
        ok = any("rust" in item.lower() for item in results)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite": "mem0_real",
            "llm": "deepseek/deepseek-chat",
            "embedder": "dashscope/text-embedding-v4",
            "results": results,
            "expected_contains": "rust",
            "ok": ok,
        }
    finally:
        shutil.rmtree(persist_dir, ignore_errors=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_mem0()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
