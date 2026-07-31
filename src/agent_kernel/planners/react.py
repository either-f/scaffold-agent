"""ReAct 策略：思考 → JSON 动作 →（工具结果回灌）→ 循环。

动作协议（模型只允许输出单个 JSON 对象）：
  {"thought": "...", "tool": "工具名", "args": {...}}
  {"thought": "...", "final": "最终答案"}

Plan-Execute / CodeAct 等新策略同样实现 PlannerPort，即插即换。
"""
from __future__ import annotations

import json

from ..ports import MemoryPort, ModelPort, PlannerPort, ToolPort
from ..types import Action, FinalAnswer, Message, RunState, ToolCall
from .context import ContextBuilder

SYSTEM_TMPL = """你是一个会使用工具的助手。可用工具：
{tools}

规则：每轮只输出一个 JSON 对象，不要输出其它任何内容。
需要工具时输出 {{"thought": "...", "tool": "<name>", "args": {{...}}}}；
可以作答时输出 {{"thought": "...", "final": "<答案>"}}。
必须完成用户明确要求的全部步骤后才能输出 final，且 final 不得为空；否则继续调用工具。
{memory}"""


PREFERENCE_QUERY = "用户的语言习惯、格式要求、风格偏好、禁忌与约束"


class ReactPlanner(PlannerPort):
    # 扩展点：子类（如 CodeActPlanner）替换 system 模板即可改变策略指令，
    # 工具描述组装、记忆检索、上下文构建、JSON 解析全部复用本类实现。
    system_template = SYSTEM_TMPL

    def __init__(
        self,
        context_builder: ContextBuilder | None = None,
        preferences: MemoryPort | None = None,
        preferences_k: int = 5,
    ) -> None:
        self.context_builder = context_builder or ContextBuilder()
        # 偏好记忆：独立 namespace 的 MemoryPort，每轮固定 query 检索、无条件注入，
        # 不像下面的常规记忆那样依赖跟当前用户输入的相关性。
        # ponytail: 写入（自动提取偏好陈述）留给离线巩固脚本做，这里只做读取注入。
        self.preferences = preferences
        self.preferences_k = preferences_k

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

        blocks: list[str] = []
        if self.preferences:
            prefs = self.preferences.search(PREFERENCE_QUERY, k=self.preferences_k)
            if prefs:
                blocks.append("已知偏好与约束（每轮都生效）：\n" + "\n".join(f"- {p}" for p in prefs))
        if memory and state.messages:
            query = next((m.content for m in reversed(state.messages) if m.role == "user"), "")
            current_context = {m.content for m in state.messages}
            hits = (
                [hit for hit in memory.search(query, k=8) if hit not in current_context][:3]
                if query
                else []
            )
            if hits:
                blocks.append("相关记忆：\n" + "\n".join(f"- {h}" for h in hits))

        system = Message(
            "system", self.system_template.format(tools=tool_desc, memory="\n".join(blocks))
        )
        prompt = self.context_builder.build(system, state, model)
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
