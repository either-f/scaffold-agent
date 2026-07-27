"""ReAct 策略：思考 → JSON 动作 →（工具结果回灌）→ 循环。

动作协议（模型只允许输出单个 JSON 对象）：
  {"thought": "...", "tool": "工具名", "args": {...}}
  {"thought": "...", "final": "最终答案"}

Plan-Execute / CodeAct 等新策略同样实现 PlannerPort，即插即换。
"""
from __future__ import annotations

import json

from ..ports import MemoryPort, ModelPort, PlannerPort, ToolPort
from ..types import Action, FinalAnswer, RunState, ToolCall

SYSTEM_TMPL = """你是一个会使用工具的助手。可用工具：
{tools}

规则：每轮只输出一个 JSON 对象，不要输出其它任何内容。
需要工具时输出 {{"thought": "...", "tool": "<name>", "args": {{...}}}}；
可以作答时输出 {{"thought": "...", "final": "<答案>"}}。
必须完成用户明确要求的全部步骤后才能输出 final，且 final 不得为空；否则继续调用工具。
{memory}"""


class ReactPlanner(PlannerPort):
    def step(
        self,
        state: RunState,
        model: ModelPort,
        tools: ToolPort,
        memory: MemoryPort | None,
    ) -> Action:
        tool_specs = tools.list_tools()
        tool_desc = "\n".join(
            f"- {t.name}: {t.description}; 参数 JSON Schema: "
            f"{json.dumps(t.parameters, ensure_ascii=False)}"
            for t in tool_specs
        ) or "(无)"
        mem_block = ""
        if memory and state.messages:
            query = next((m.content for m in reversed(state.messages) if m.role == "user"), "")
            current_context = {m.content for m in state.messages}
            hits = (
                [hit for hit in memory.search(query, k=8) if hit not in current_context][:3]
                if query
                else []
            )
            if hits:
                mem_block = "相关记忆：\n" + "\n".join(f"- {h}" for h in hits)

        from ..types import Message  # 局部 import 避免循环

        prompt = [Message("system", SYSTEM_TMPL.format(tools=tool_desc, memory=mem_block))]
        prompt += state.messages
        output = model.complete(prompt, tool_specs)
        return self._parse(output.text)

    @staticmethod
    def _parse(text: str) -> Action:
        try:
            start, end = text.find("{"), text.rfind("}")
            obj = json.loads(text[start : end + 1])
        except Exception:
            return FinalAnswer(content=text.strip())  # 解析失败：宽容降级为直接回答
        thought = str(obj.get("thought", ""))
        if "tool" in obj:
            return ToolCall(name=obj["tool"], args=obj.get("args", {}), thought=thought)
        return FinalAnswer(content=str(obj.get("final", "")), thought=thought)
