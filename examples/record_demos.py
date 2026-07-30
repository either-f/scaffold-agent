r"""录制三个端到端 demo 为确定性 asciicast v2 .cast 文件，纯标准库实现。

用法:
  python examples/record_demos.py                # 录制全部三个 demo 并写入 .cast 文件
  python examples/record_demos.py --check        # CI 模式：录制并逐字节比对已跟踪的 .cast 文件
  python examples/record_demos.py --play <name>  # 回放指定 .cast 到终端

确定性保证:
- 固定宽度 80、高度 24
- 省略可选时间戳字段（header 不含 timestamp）
- 事件偏移单调递增（每行 +0.1s）
- 统一 \n 行尾
"""
from __future__ import annotations

import argparse
import json
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

CAST_WIDTH = 80
CAST_HEIGHT = 24
LINE_DELTA = 0.1

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
}


def _run_demo(script: str) -> tuple[int, str, str]:
    """运行单个 demo，返回 (exit_code, stdout, stderr)。"""
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "examples" / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(PROJECT_ROOT),
        timeout=30,
        env={**__import__("os").environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _demo_to_cast_bytes(stdout: str, stderr: str = "") -> bytes:
    """将 demo stdout/stderr 转换为确定性 asciicast v2 字节序列。"""
    header = {"version": 2, "width": CAST_WIDTH, "height": CAST_HEIGHT}
    header_line = json.dumps(header, ensure_ascii=False, separators=(",", ":"))

    combined = stdout
    if stderr:
        combined = stderr + "\n" + stdout

    lines: list[bytes] = [header_line.encode("utf-8") + b"\n"]
    offset = 0.0
    for line in combined.splitlines(True):
        entry = [offset, "o", line]
        entry_line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        lines.append(entry_line.encode("utf-8") + b"\n")
        offset += LINE_DELTA

    return b"".join(lines)


def _record_demo(name: str, script: str) -> tuple[Path | None, bytes]:
    """运行 demo 并生成确定性 asciicast v2 字节。返回 (path, cast_bytes)。"""
    print(f"{COLORS['bold']}[录制] {name}{COLORS['reset']}")

    code, stdout, stderr = _run_demo(script)

    if code != 0:
        print(f"  {COLORS['red']}失败{COLORS['reset']}: exit={code}")
        if stderr:
            print(stderr[-500:])
        return None, b""

    cast_bytes = _demo_to_cast_bytes(stdout, stderr)

    line_count = cast_bytes.count(b"\n")
    out_path = OUT_DIR / f"{name}.cast"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(cast_bytes)

    print(f"  {COLORS['green']}OK{COLORS['reset']} -> {out_path} ({line_count} 行)")
    return out_path, cast_bytes


def _check_demo(name: str, script: str) -> bool:
    """CI 检查：运行 demo、生成确定性 asciicast、逐字节比对已跟踪 .cast。"""
    print(f"{COLORS['bold']}[检查] {name}{COLORS['reset']}")

    code, stdout, stderr = _run_demo(script)
    if code != 0:
        print(f"  {COLORS['red']}FAIL{COLORS['reset']} demo exit={code}")
        if stderr:
            print(stderr[-300:])
        return False

    expected = _demo_to_cast_bytes(stdout, stderr)
    cast_path = OUT_DIR / f"{name}.cast"

    if not cast_path.exists():
        print(f"  {COLORS['red']}FAIL{COLORS['reset']} 缺少跟踪文件: {cast_path}")
        return False

    tracked = cast_path.read_bytes()

    if expected != tracked:
        print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast 字节不匹配（已重新生成）")
        return False

    # 验证 header 与事件形状
    try:
        lines = tracked.decode("utf-8").splitlines()
        if not lines:
            print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast 为空")
            return False
        header = json.loads(lines[0])
        if not isinstance(header, dict) or header.get("version") != 2:
            print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast header 无效")
            return False
        for i, line in enumerate(lines[1:], start=1):
            entry = json.loads(line)
            if not isinstance(entry, list) or len(entry) != 3:
                print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast 第 {i} 行事件格式无效")
                return False
            t, ev_type, data = entry
            if not isinstance(t, (int, float)) or ev_type != "o" or not isinstance(data, str):
                print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast 第 {i} 行事件字段无效")
                return False
    except json.JSONDecodeError as e:
        print(f"  {COLORS['red']}FAIL{COLORS['reset']} {name}.cast JSON 解析失败: {e}")
        return False

    print(f"  {COLORS['green']}PASS{COLORS['reset']} {name} ({len(lines)-1} 帧, 逐字节匹配)")
    return True


def _play_cast(path: Path) -> None:
    """在终端回放 .cast 文件（使用相对事件偏移，不依赖 header timestamp）。"""
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    if not events:
        return

    _, *entries = events

    start = time.time()
    for entry in entries:
        t, ev_type, data = entry
        elapsed = time.time() - start
        delay = max(t - elapsed, 0.001)
        time.sleep(delay)
        sys.stdout.write(data)
        sys.stdout.flush()

    print(f"\n{COLORS['dim']}--- 回放完成 ---{COLORS['reset']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="录制 agent-kernel 演示为确定性 asciicast v2 文件")
    parser.add_argument("--check", action="store_true", help="CI 模式：录制并逐字节比对已跟踪的 .cast 文件")
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
        print(f"{COLORS['bold']}Demo 确定性检查{COLORS['reset']}")
        ok = all(_check_demo(n, s) for n, s in DEMOS.items())
        return 0 if ok else 1

    print(f"{COLORS['bold']}录制 agent-kernel 演示{COLORS['reset']}")
    print(f"输出目录: {OUT_DIR}")
    print()

    failed: list[str] = []
    for name, script in DEMOS.items():
        path, _ = _record_demo(name, script)
        if path is None:
            failed.append(name)
        print()

    success = [n for n in DEMOS if n not in failed]
    print(f"{COLORS['bold']}结果{COLORS['reset']}")
    print(f"  成功: {len(success)}/3 {success if success else '无'}")
    if failed:
        print(f"  失败: {failed}")
        print()
        print(f"{COLORS['yellow']}回放:{COLORS['reset']} python examples/record_demos.py --play <name>")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
