# ADR-0013: agentscope 接成 WorkerDelegationPort 的 worker，不做新 PlannerPort

日期: 2026-08-11  状态: 已采纳

## 决策

- agentscope 2.x（`agentscope.agent.Agent`）**不**包成新的 `PlannerPort` 策略。
- 新增 `AgentScopeWorker`（`adapters/tools/agentscope_worker.py`），实现一个新的
  `Worker` 协议（`run(task: str) -> str`），跟 `WorkerDelegationPort` 现有的
  `AgentKernel` worker 平级注册进同一张委派表。
- `WorkerDelegationPort`（`adapters/tools/agents.py`）从"硬编码只接受 `AgentKernel`"
  放宽成"接受 `AgentKernel` 或 `Worker` 协议"：`call()` 内部按 `isinstance(worker,
  AgentKernel)` 分支，`AgentKernel` 路径逐字节保留原语义（取 `RunState.status`/
  `.answer`），新协议路径直接拿 `run()` 的返回字符串，两条路径都在失败/空答案时抛
  `RuntimeError`。这是 M9 `SandboxRunner(Protocol)` 放宽 `SandboxToolbox` 类型标注的
  同一套手法，第二次在这个仓库里用同一模式解决"新实现要不要塞进已有硬编码类型"的问题。

## 原因

先读了 agentscope 2.x 的真实 API（不是只看 GitHub 简介）：`Agent` 类本身是一个自带
`model`+`toolkit`+ReAct 循环+中间件+事件流的完整运行时，构造时接收 `ChatModelBase` 和
`Toolkit`，对外只暴露 `reply(inputs) -> Msg` 这样的整轮对话接口，不是"给定 `state`，
决定下一步 `ToolCall` 或 `FinalAnswer`"的单步决策函数——跟本仓库 `PlannerPort` 的契约
（`step(state, model, tools, memory)`）语义完全不对齐。硬套成 `PlannerPort` 意味着要
把 agentscope 自己的 ReAct 循环拆开、只借一步决策，等于放弃了 agentscope 真正有价值的
部分（它自己的循环、权限系统、事件系统），纯属为了"占用 PlannerPort 这个坑位"而削足适履。

agentscope 官方文档里真正对应"多 agent 编排"的概念是 **Agent Team**（leader–worker
orchestration，内置任务规划），语义上跟本仓库 M6 自研的 `WorkerDelegationPort`
（orchestrator-worker）同级——都是"一个上层 agent 把任务委派给下层 agent，取回结果"。
因此接入点选在 worker 层，而不是 planner 层。

## 后果与迁移条件

- `WorkerDelegationPort` 的类型放宽是唯一的既有代码改动，且是加法：所有既有调用方
  （`register(name, AgentKernel实例, ...)`）行为逐字节不变，`tests/test_worker_delegation.py`
  的 `test_agent_kernel_worker_path_unchanged` 覆盖这条回归。
- `AgentScopeWorker.run()` 用 `asyncio.run()` 包一次 `Agent.reply()`（agentscope 的
  `reply` 原生是协程）；如果调用方本身已经在事件循环里（比如未来某天 `AgentKernel` 自己
  变成 async），这里会撞 `asyncio.run() cannot be called from a running event loop`——
  当前 `AgentKernel.run()` 是同步的，不构成问题，留一条 ponytail 注释在后续 async 化时
  处理。
- 验收：`evals/run_agentscope_worker.py` 真实跑通——本仓库 `AgentKernel`（`ReactPlanner`
  + `LiteLLMModel`/DeepSeek）通过 `WorkerDelegationPort` 把算术任务委派给一个真实
  `agentscope.agent.Agent`（`DeepSeekChatModel`），拿回正确答案，见
  [evals/baseline-m10-agentscope-worker-real.json](evals/baseline-m10-agentscope-worker-real.json)。
  `tests/test_worker_delegation.py` 6 项离线单测覆盖两条路径的分支逻辑、失败/空答案/
  异常传播、内层工具透传不受影响。
- 没有触碰 `kernel.py` / `ports.py`；`PlannerPort` 契约不变，未来如果出现一个真正
  "单步决策"形态的多 agent 框架，仍然可以照 M0 手册接成新 `PlannerPort`，跟这次的
  worker 路线并不冲突。
- agentscope 自身的 Memory 模块文档里说"可切换 ReMe/Mem0 后端"、Workspace 模块支持
  Daytona——这些是 agentscope 内部的选型，跟本仓库 M8（`Mem0Memory`）、M9
  （`DaytonaSandbox`）互不依赖、互不冲突，只是同一批开源选型在两边各自被复用了一次。
