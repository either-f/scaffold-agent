# ADR-0001: 微内核 + 端口架构

日期: 2026-07-27  状态: 已采纳

## 决策
内核只实现"组装上下文→调模型→分发动作→更新状态"循环，其余全部定义为端口
（Model/Tool/Memory/Planner/Skill/Interop）+ 横切件（EventBus/Checkpoint）。
第三方依赖只允许出现在 adapters/ 与 planners/。

## 借鉴来源
- checkpoint/interrupt 语义: LangGraph
- hooks/子 agent/给 agent 一台电脑: Claude Agent SDK
- handoff/guardrail 概念: OpenAI Agents SDK
- 记忆接口形态: Mem0；分层: Letta(MemGPT)
- 技能规范: Anthropic Agent Skills (SKILL.md)

## 后果
+ 新概念接入 = 新 adapter/策略，内核零改动（见 PLAN.md 第 7 节）
+ 内核可离线测试（Fake adapter 纪律）
- 多写一层抽象的成本；用 ADR 与 CI 检查控制抽象漂移
