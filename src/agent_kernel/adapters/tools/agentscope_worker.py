"""把 agentscope.agent.Agent 包成 WorkerDelegationPort 的 worker，跟自研 AgentKernel
worker 平级挂进同一个委派表——见 ADR-0013 为什么接成 worker 而非新 PlannerPort。
"""
from __future__ import annotations

import asyncio


class AgentScopeWorker:
    """`Worker` 协议实现：`run(task) -> str`。内部用 `asyncio.run()` 跑一次
    agentscope 的 `Agent.reply()`（原生异步 API），只取回复的纯文本内容。
    """

    def __init__(self, agent: "object") -> None:
        self._agent = agent

    def run(self, task: str) -> str:
        from agentscope.message import UserMsg

        async def _reply() -> str:
            reply = await self._agent.reply(UserMsg("user", task))
            return reply.get_text_content() or ""

        return asyncio.run(_reply())
