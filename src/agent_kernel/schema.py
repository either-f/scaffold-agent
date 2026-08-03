"""最小结构化 JSON Schema 校验器（覆盖本仓库工具实际使用的子集）。

只支持：type: object + properties + required + 每个属性的基础类型检查
（string/number/integer/boolean/array/object）。不支持 $ref/oneOf/pattern/format 等
Draft-07/2020-12 完整特性——本仓库核心无第三方依赖（见 pyproject.toml），手写最小校验
足以挡住真实失败模式（模型臆造字段、漏掉必填字段、类型错配）。
"""
from __future__ import annotations

from typing import Any


class ToolArgumentValidationError(ValueError):
    """工具调用参数不符合其声明的 JSON Schema。

    携带工具名与具体违规描述（缺必填字段、类型不匹配等），不做静默修正或丢弃字段。
    """

    def __init__(self, tool_name: str, detail: str) -> None:
        self.tool_name = tool_name
        self.detail = detail
        super().__init__(f"工具 {tool_name} 参数校验失败: {detail}")


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": (int, float),
    "array": list,
    "object": dict,
}


def validate_arguments(tool_name: str, parameters: dict[str, Any], args: Any) -> None:
    """校验 args 是否符合 parameters 声明的 JSON Schema（最小子集）。

    不匹配时抛 ToolArgumentValidationError。parameters 为空字典时视为无约束，直接放行
    （兼容未声明 parameters 的旧工具）。
    """
    if not parameters:
        return
    if not isinstance(args, dict):
        raise ToolArgumentValidationError(
            tool_name, f"期望 object，实际 {type(args).__name__}"
        )
    props = parameters.get("properties") or {}
    required = parameters.get("required") or []
    for field_name in required:
        if field_name not in args:
            raise ToolArgumentValidationError(
                tool_name, f"缺少必填字段: {field_name}"
            )
    for key, value in args.items():
        if key not in props:
            # 未声明的额外字段视为违规（模型臆造字段的真实失败模式）
            raise ToolArgumentValidationError(
                tool_name, f"存在未声明的字段: {key}"
            )
        schema = props[key] or {}
        declared_type = schema.get("type")
        if declared_type is None:
            continue
        expected = _JSON_TYPES.get(declared_type)
        if expected is None:
            # 未知的 JSON Schema 类型名：保守放行，不在这层挡
            continue
        # bool 是 int 的子类，但 JSON Schema 中 integer 不应接受 bool
        if declared_type == "integer" and isinstance(value, bool):
            raise ToolArgumentValidationError(
                tool_name, f"字段 {key} 期望 integer，实际 bool"
            )
        if declared_type == "number" and isinstance(value, bool):
            raise ToolArgumentValidationError(
                tool_name, f"字段 {key} 期望 number，实际 bool"
            )
        if not isinstance(value, expected):
            raise ToolArgumentValidationError(
                tool_name,
                f"字段 {key} 期望 {declared_type}，实际 {type(value).__name__}",
            )
