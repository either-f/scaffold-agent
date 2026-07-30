"""检查内核目录不含第三方 import（CI 门禁脚本）。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "agent_kernel"
STDLIB = sys.stdlib_module_names
FAIL = 0

for f in ROOT.rglob("*.py"):
    rel = f.relative_to(ROOT)
    if rel.parts[0] in ("adapters", "planners"):
        continue
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod not in STDLIB:
                    print(f"THIRD-PARTY IMPORT: {f} imports {alias.name}", file=sys.stderr)
                    FAIL += 1
        elif isinstance(node, ast.ImportFrom):
            if node.level is not None and node.level > 0:
                continue
            if node.module is None:
                continue
            mod = node.module.split(".")[0]
            if mod not in STDLIB:
                print(f"THIRD-PARTY IMPORT: {f} imports {node.module}", file=sys.stderr)
                FAIL += 1

sys.exit(min(FAIL, 1))
