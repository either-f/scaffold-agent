# ADR-0006: 图记忆生产 adapter 换用 Neo4j

日期: 2026-07-30  状态: 已采纳

## 决策

- 新增 `Neo4jGraphMemory`（`adapters/memory_graph_neo4j.py`），实现 `MemoryPort.add/search`
  存查原文事实，并提供 `add_edge/search_edges/get_neighbors` 存查 subject/relation/object
  三元组，底层是真实 Neo4j（Cypher + 官方 `neo4j` Python driver），非模拟。
- 原有 `GraphMemory`（SQLite，`adapters/memory_graph.py`）保留，降级为该 adapter 的
  Fake 对照，供离线测试用，接口方法名一致但不再是生产实现。
- namespace 在构造时固定为实例属性（对齐 `PgVectorMemory` 的单实例单 namespace 模式），
  边/邻居查询方法不再像 SQLite 版本那样每次调用都显式传 namespace 参数。
- 去重通过应用层 `sha256(namespace|...)` 计算 `dedup_key`，配合 `MERGE` 做幂等写入；
  唯一性约束建在单一 `dedup_key` 属性上（Neo4j Community 只支持单属性唯一约束，
  组合唯一约束/node key 是 Enterprise 特性，不能直接对 (namespace, subject, relation, object)
  建组合约束）。
- 关系统一建为单一类型 `:REL {type: relation}`，不使用动态关系类型，因为动态关系类型
  依赖 APOC 插件，官方镜像默认不带；保持零插件依赖。
- 依赖声明为可选 extra `graph = ["neo4j>=5.26,<6"]`；`compose.yaml` 新增 `neo4j` 服务
  （`neo4j:5.26-community`，bolt 7687 + http 7474，健康检查，独立 volume）。
- 新增 `evals/run_graph.py`，对真实容器验证：事实去重、tool 角色过滤、边去重、
  按 subject/relation/object 过滤查询、双向邻居查询、namespace 隔离。

## 原因

M6 交付时的 `GraphMemory` 只是验证 `MemoryPort` 扩展性的最小自研实现（SQLite 存三元组），
没有真图数据库能力（无原生图遍历、无 Cypher、无生产运维形态）。要把项目往生产方向推进，
图记忆需要能扛住真实的关系查询和图遍历负载，因此换成 Neo4j：官方 Python driver 成熟、
Cypher 查询表达力强、有免费 Community docker 镜像可离线自托管，符合"融合纪律"里
"直接采用当依赖"的优先级（优于自建图存储或引入不成熟驱动）。

## 后果与迁移条件

- CI 离线门禁不跑 `run_graph.py`（需要真实 Neo4j 容器），保持 `GraphMemory` 承担
  CI 里的离线回归职责；`run_graph.py` 是需要 `docker compose up -d neo4j` 后手动/CD 环境跑的验收脚本。
- 关系查询目前是精确属性匹配 + 单类型 `:REL`；出现多跳路径查询或需要按关系类型建索引
  提速时，再加 `r.type` 上的索引或引入更细粒度关系类型建模。
- 内核对本次改动零感知：只新增一个 adapter 文件与一个 eval 脚本，未触碰 `kernel.py`
  / `ports.py`，验证了 ADR-0001 的扩展性承诺。
