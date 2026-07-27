# ADR-0003: M2 恢复与重试语义

日期: 2026-07-27  状态: 已采纳

## 决策

- checkpoint 保存待执行工具；`paused` 表示仍需审批，`running + pending_tool`
  表示已经批准、等待执行。
- 恢复采用 at-least-once。工具完成但结果尚未 checkpoint 时进程退出，恢复可能重复调用。
- 模型超时与重试交给 LiteLLM；MCP 仅对显式标记的只读工具重试一次。
- JSON checkpoint 用同目录临时文件与 `os.replace()` 原子更新。

## 原因

M2 只开放读取型 MCP 工具，重复读取没有外部副作用。通用 exactly-once 需要工具端幂等键
或持久调用日志，不应在尚无写工具时提前引入。

## 后果与迁移条件

- 同一 `run_id` 不允许多个进程并发恢复。
- approval 异常保持 paused checkpoint，恢复时 fail-closed 并重新审批。
- 未来开放写入、移动或删除工具前，必须增加幂等键或调用日志；这些工具默认不得自动重试。
