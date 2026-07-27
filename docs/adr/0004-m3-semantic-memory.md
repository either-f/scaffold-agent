# ADR-0004: M3 语义记忆边界

日期: 2026-07-27  状态: 已采纳

## 决策

- 保持 `MemoryPort.add/search` 不变，以 namespace 隔离用户，以 run_id 保留来源。
- 保存原始 user 与最终 assistant 消息；tool 结果不进入长期记忆，也不逐轮调用 LLM 抽取事实。
- 使用 LiteLLM 调用 `dashscope/text-embedding-v4`，以 PostgreSQL + pgvector 做精确余弦检索。
- 每次操作按需建立数据库连接，不增加连接池或 ANN 索引。

## 原因

M3 数据量很小，精确检索已经足够。原消息入库可直接复用现有端口和内核调用点，避免把
Mem0 的框架、事实更新协议与额外模型调用一并引入。

## 后果与迁移条件

- 一个 `PgVectorMemory` 实例只服务一个 namespace；需要多租户 API 时再显式增加 user_id。
- 记忆规模或查询延迟出现实测瓶颈后再增加连接池和 HNSW。
- 需要消歧、事实更新或删除时再引入抽取层；首版不提供 update/delete。
