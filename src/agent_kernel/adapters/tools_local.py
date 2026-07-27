"""本地函数工具箱：把普通 Python 函数注册成工具。

与 MCP 工具实现同一 ToolPort 接口，对内核完全等价。
"""
from __future__ import annotations

import ast
import datetime
import operator
from typing import Callable

from ..ports import ToolPort
from ..types import ToolSpec


class LocalToolbox(ToolPort):
    def __init__(self) -> None:
        self._fns: dict[str, Callable[..., str]] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(self, name: str, description: str, fn: Callable[..., str], parameters: dict | None = None) -> None:
        self._fns[name] = fn
        self._specs[name] = ToolSpec(name=name, description=description, parameters=parameters or {})

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def call(self, name: str, args: dict) -> str:
        if name not in self._fns:
            raise KeyError(f"未知工具: {name}")
        return str(self._fns[name](**args))


# --------------------------------------------------------------- demo tools
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
}


def safe_calc(expression: str) -> str:
    """只允许数字与四则运算的安全计算器（演示"沙箱思维"的最小样例）。"""

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"不允许的表达式节点: {type(node).__name__}")

    result = _eval(ast.parse(expression, mode="eval"))
    return str(int(result)) if float(result).is_integer() else str(result)


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_toolbox() -> LocalToolbox:
    box = LocalToolbox()
    box.register("now", "获取当前日期时间", now)
    box.register(
        "calc", "计算一个四则运算表达式", safe_calc,
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
    )
    return box
