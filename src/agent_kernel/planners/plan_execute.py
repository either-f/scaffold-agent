"""Plan-Execute 策略：首步制定计划存入上下文，后续步骤复用 ReAct 执行。

与 LangChain Plan-and-Execute 思想一致：先规划再执行；区别是这里不引入第二个
运行时，而是在首个 kernel step 中一并获取计划与首个动作，后续步骤直接走 ReAct。
"""
from __future__ import annotations

import json

from ..types import Action, Message, RunState
from .react import ReactPlanner

PLAN_SYSTEM_TMPL = """你是一个会制定计划并使用工具的助手。可用工具：
{tools}

规则：每轮只输出一个 JSON 对象，不要输出其它任何内容。
首先制定简短计划，然后立即执行第一步：
- 需要工具时输出 {{"thought": "...", "plan": "<简短计划>", "tool": "<name>", "args": {{...}}}}
- 可以作答时输出 {{"thought": "...", "plan": "<简短计划>", "final": "<答案>"}}
plan 不得为空。必须完成全部步骤后才能输出 final，且 final 不得为空；否则继续调用工具。
{memory}"""


class PlanExecutePlanner(ReactPlanner):
    """Plan-Execute 策略：首步制定计划存入 RunState.messages，后续步骤复用 ReAct 执行。

    不引入第二个运行时：计划获取与首个动作在同一 model 调用中完成，
    后续 kernel step 直接走父类 ReactPlanner.step()，计划已可见于上下文。
    """

    def step(
        self,
        state: RunState,
        model,
        tools,
        memory,
    ) -> Action:
        if state.step > 1:
            return super().step(state, model, tools, memory)
        return self._plan_and_act(state, model, tools, memory)

    def _plan_and_act(self, state: RunState, model, tools, memory) -> Action:
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

        system = Message("system", PLAN_SYSTEM_TMPL.format(tools=tool_desc, memory=mem_block))
        prompt = self.context_builder.build(system, state, model)
        output = model.complete(prompt, tool_specs)
        action, final_output = self._resolve(model, prompt, tool_specs, output)

        plan = self._extract_plan(final_output.text)
        if plan:
            state.add("assistant", f"[计划] {plan}")

        return action

    @staticmethod
    def _extract_plan(text: str) -> str:
        try:
            start, end = text.find("{"), text.rfind("}")
            obj = json.loads(text[start : end + 1])
            return str(obj.get("plan", "")).strip()
        except Exception:
            return ""
