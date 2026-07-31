"""真实编译的 LangGraph StateGraph，喂给 `LangGraphPlanner` 用——不是脚本化假图。

图结构：classify 节点判断"工具结果已在最近一条消息里"/"问题含算术表达式且有 calc 工具"/
"两者都不是"，用 `add_conditional_edges` 路由到三个终止节点之一，分别产出
FinalAnswer / ToolCall。真实验证 LangGraphPlanner 的输出契约（`final` 或 `tool` 字段）
能驱动一个真正编译的 StateGraph，而不只是回放预先写好的输出序列。
"""
from __future__ import annotations

import re
from typing import Any, TypedDict

ARITHMETIC_RE = re.compile(r"\d+(?:\.\d+)?\s*[+\-*/]\s*\d+(?:\.\d+)?")


class GraphState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    step: int
    context_summary: str
    final: str
    tool: str
    args: dict[str, Any]
    thought: str
    _route: str
    _expr: str
    _query: str
    _tool_result: str


def _last_user_message(state: GraphState) -> str:
    for message in reversed(state.get("messages", [])):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _last_tool_result(state: GraphState) -> str | None:
    messages = state.get("messages", [])
    if messages and messages[-1].get("role") == "tool":
        return str(messages[-1].get("content", ""))
    return None


def classify_node(state: GraphState) -> dict[str, Any]:
    tool_result = _last_tool_result(state)
    if tool_result is not None:
        return {"_route": "answer_with_result", "_tool_result": tool_result}

    query = _last_user_message(state)
    known_tools = {t["name"] for t in state.get("tools", [])}
    match = ARITHMETIC_RE.search(query)
    if match and "calc" in known_tools:
        return {"_route": "call_tool", "_expr": match.group(0)}
    return {"_route": "answer_direct", "_query": query}


def call_tool_node(state: GraphState) -> dict[str, Any]:
    expr = state["_expr"]
    return {"tool": "calc", "args": {"expression": expr}, "thought": f"识别到算术表达式 {expr}，调用 calc"}


def answer_with_result_node(state: GraphState) -> dict[str, Any]:
    return {"final": f"计算结果：{state['_tool_result']}", "thought": "已拿到工具结果，给出最终答案"}


def answer_direct_node(state: GraphState) -> dict[str, Any]:
    query = state.get("_query") or ""
    return {"final": query or "我不确定该怎么回答。", "thought": "无需工具，直接回答"}


def build_graph() -> Any:
    """编译一个真实的 langgraph StateGraph；需要 `langgraph` 包（extras: langgraph）。"""
    from langgraph.graph import END, StateGraph

    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("classify", classify_node)
    builder.add_node("call_tool", call_tool_node)
    builder.add_node("answer_with_result", answer_with_result_node)
    builder.add_node("answer_direct", answer_direct_node)
    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        lambda state: state["_route"],
        {
            "call_tool": "call_tool",
            "answer_with_result": "answer_with_result",
            "answer_direct": "answer_direct",
        },
    )
    builder.add_edge("call_tool", END)
    builder.add_edge("answer_with_result", END)
    builder.add_edge("answer_direct", END)
    return builder.compile()
