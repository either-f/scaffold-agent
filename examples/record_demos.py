"""录制三个端到端 demo 为 asciicast v2 .cast 文件，标准库实现。

用法:
  python examples/record_demos.py                # 录制全部三个 demo
  python examples/record_demos.py --check        # 仅运行验证（用于 CI）
  python examples/record_demos.py --play <name>  # 回放指定 .cast 到终端

.cast 文件写入 docs/demos/，格式兼容 asciinema player 与 agg。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "docs" / "demos"

DEMOS = {
    "research": "demo_research.py",
    "files": "demo_files.py",
    "ops": "demo_ops.py",
}

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
}


def _term_size() -> tuple[int, int]:
    try:
        cols, rows = os.get_terminal_size()
        return max(cols, 80), max(rows, 24)
    except (OSError, ValueError):
        return 80, 24


def _run_demo(script: str) -> tuple[int, str, str]:
    """运行单个 demo，返回 (exit_code, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "examples" / script)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _record_demo(name: str, script: str) -> Path | None:
    """运行 demo 并以 asciicast v2 格式记录，返回 .cast 文件路径。"""
    print(f"{COLORS['bold']}[录制] {name}{COLORS['reset']}")

    start = time.time()
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "examples" / script)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    elapsed = time.time() - start

    if proc.returncode != 0:
        print(f"  {COLORS['red']}失败{COLORS['reset']}: exit={proc.returncode}")
        print(proc.stderr[-500:])
        return None

    width, height = _term_size()
    header = {
        "version": 2,
        "width": width,
        "height": height,
        "timestamp": int(start),
        "title": f"agent-kernel {name} demo",
    }

    combined = proc.stdout
    if proc.stderr:
        combined = proc.stderr + "\n" + proc.stdout

    lines: list[list] = []
    t_start = start
    for line in combined.splitlines(True):
        line_time = time.time() if line == combined.splitlines(True)[-1] else t_start + elapsed * 0.5
        offset = line_time - t_start
        lines.append([offset, "o", line])

    out_path = OUT_DIR / f"{name}.cast"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for entry in lines:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"  {COLORS['green']}OK{COLORS['reset']} → {out_path} ({len(lines)} 帧, {elapsed:.1f}s)")
    return out_path


def _check_demo(name: str, script: str) -> bool:
    """CI 检查模式：只运行验证。"""
    code, stdout, stderr = _run_demo(script)
    if code != 0:
        print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name} exit={code}")
        if stderr:
            print(stderr[-300:])
        return False
    print(f"  {COLORS['green']}PASS{COLORS['reset']} {name}")
    return True


def _play_cast(path: Path) -> None:
    """在终端回放 .cast 文件（简易线性回放）。"""
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    header, *entries = events
    width = header.get("width", 80)

    for entry in entries:
        t, ev_type, data = entry
        time.sleep(max(t - (time.time() - float(header["timestamp"])), 0.001))
        sys.stdout.write(data)
        sys.stdout.flush()

    print(f"\n{COLORS['dim']}— 回放完成 —{COLORS['reset']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="录制 agent-kernel 演示为 asciicast v2 文件")
    parser.add_argument("--check", action="store_true", help="CI 模式：仅运行验证")
    parser.add_argument("--play", metavar="NAME", help=f"回放指定 demo: {', '.join(DEMOS)}")
    args = parser.parse_args()

    if args.play:
        name = args.play
        if name not in DEMOS:
            print(f"未知 demo: {name}，可选: {', '.join(DEMOS)}", file=sys.stderr)
            return 1
        _play_cast(OUT_DIR / f"{name}.cast")
        return 0

    if args.check:
        print(f"{COLORS['bold']}Demod 检查{COLORS['reset']}")
        ok = all(_check_demo(n, s) for n, s in DEMOS.items())
        return 0 if ok else 1

    print(f"{COLORS['bold']}录制 agent-kernel 演示{COLORS['reset']}")
    print(f"输出目录: {OUT_DIR}")
    print()

    results: dict[str, str | None] = {}
    for name, script in DEMOS.items():
        results[name] = _record_demo(name, script)
        print()

    success = [n for n, p in results.items() if p is not None]
    failed = [n for n, p in results.items() if p is None]

    print(f"{COLORS['bold']}结果{COLORS['reset']}")
    print(f"  成功: {len(success)}/3 {success if success else '无'}")
    if failed:
        print(f"  失败: {failed}")
        print()
        print(f"{COLORS['yellow']}回放:{COLORS['reset']} python examples/record_demos.py --play <name>")
        print(f"{COLORS['yellow']}外部播放:{COLORS['reset']} asciinema play docs/demos/<name>.cast")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
