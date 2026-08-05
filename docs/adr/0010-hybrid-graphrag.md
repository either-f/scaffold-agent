# ADR-0010: Hybrid GraphRAG——向量 + BM25 关键词 + 图谱三路召回，RRF 融合

日期: 2026-08-04  状态: 已采纳

## 决策

- 新增 `MilvusMemory`（`adapters/memory/milvus.py`）：向量检索腿，`MemoryPort.add/search`
  底层是真实 Milvus（`pymilvus.MilvusClient`），`uri` 对本地文件（Milvus Lite 嵌入模式）
  和真实 server（`http://host:19530`）透明，同一份代码两种模式都能跑，不用单独写 Fake。
- 新增 `Bm25Memory`（`adapters/memory/bm25.py`）：不是占位 Fake，是纯 stdlib 手写的
  BM25Okapi 算法（k1=1.5, b=0.75），SQLite 持久化语料，CJK 无空格文字退化成字符
  2-gram 分词（复用 `evals/run_consolidation.py` 的 `_shingles()` 思路）。
- 新增 `ElasticsearchMemory`（`adapters/memory/elasticsearch.py`）：关键词检索腿生产
  实现，BM25 是 ES 默认 similarity，零配置；namespace 映射成 index，`_id=content_hash`
  幂等写入去重。`Bm25Memory` 是它的离线 Fake 对照（跟 `GraphMemory` 之于
  `Neo4jGraphMemory` 一样的关系），同时自身也是一个能打的轻量 BM25 后端。
- 图谱检索腿复用现有 `GraphMemory`/`Neo4jGraphMemory`，不新增 adapter；新增
  `multi_hop_search()`（`adapters/memory/hybrid_rag.py`）做多跳扩展——BFS 遍历，
  通过调用方注入的 `neighbors_fn` 闭包屏蔽两个 adapter `get_neighbors` 签名不一致的
  问题（SQLite 版要 namespace 参数、Neo4j 版不要，见 ADR-0006），不改任何一个现有
  adapter 文件。
- 新增 `reciprocal_rank_fusion()`（同文件）：标准 RRF，`score(d) = Σ 1/(k + rank_in_list)`，
  k=60 是原论文（Cormack et al., 2009）的标准常数。纯函数，独立可测。
- 新增 `HybridGraphRAG`（同文件）：组合三路召回器 + RRF 融合，本身也实现 `MemoryPort`，
  跟 `CompositeMemory` 一样内核零感知。`add()` 只转发给 vector+keyword 两路原始内容
  索引，graph 的实体/关系写入是离线 ingestion 的职责（LLM 抽取成本高，不能挂在同步
  `add()` 路径上，跟 `CompositeMemory.add()` 只落 episodic 不重复写 semantic 是同一个
  理由）。
- 新增 `evals/run_hybrid_rag.py`：离线批处理脚本，跟 `run_consolidation.py` 同一套
  `--mode offline|real` 路数——`clean_markdown()`/`chunk_markdown()`（纯 stdlib 正则）
  清洗切片，`ENTITY_EXTRACTION_PROMPT`（仿 `run_consolidation.py` 的
  `EXTRACTION_PROMPT` 风格）驱动 LLM 抽取实体/关系写 `graph.add_edge()`。
- 依赖声明为两个新 extra：`milvus = ["pymilvus[milvus_lite]>=2.4,<3"]`、
  `keyword = ["elasticsearch>=8,<9"]`；`compose.yaml` 新增 `elasticsearch` 服务
  （单节点、禁鉴权、健康检查、独立 volume），Milvus 不加 compose 服务（见"后果"）。

## 原因

向量检索（语义相似）、关键词检索（精确词项/术语匹配）、图检索（结构化关系/多跳
推理）三种召回方式互补，各自有召回不到的场景：向量检索找不准生僻术语/精确数字，
关键词检索找不到语义相关但用词不同的内容，两者都做不到"通过实体关系链路推理出
文档里没有直接写的关联事实"——这正是图谱多跳扩展的价值，RRF 融合三路排序缓解
"单路召回不全、多路简单拼接又不知道怎么加权"的问题（RRF 不需要各路分数可比，
只看排名，天然适合异构检索器融合）。

选 Milvus 而不是复用现有 `PgVectorMemory` 当向量腿：简历/需求明确点名 Milvus，且
`pymilvus.MilvusClient` 统一了本地嵌入模式与远程 server 模式的调用接口，理论上比
pgvector 更适合本地开发验证（见"后果"里的平台限制）。

BM25 选择手写而不是只包一层 Elasticsearch：算法本身足够简单（几十行 stdlib），手写
版能在没有真实 ES 的环境里离线验证融合逻辑对不对，也符合"融合纪律"第 3 条——
每个真 adapter 都要有 Fake 对照。

## 后果与迁移条件

- **Milvus Lite 在 Windows 上验证不了**：`milvus-lite` 包官方只发布 Linux/macOS wheel，
  这次会话在 Windows 环境下 `pip install pymilvus[milvus_lite]` 后 `pymilvus` 装得上，
  但 `MilvusClient(uri="./xxx.db")` 嵌入模式直接报
  `ConnectionConfigException: milvus-lite is required`（已实测）。`MilvusMemory` 因此
  降级成跟 pgvector/neo4j 当初一样"未在本次会话验证"的状态，真要验证需要
  Linux/macOS 环境或一个真实 Milvus server。因为这个限制，`compose.yaml` 没加
  Milvus 服务——加了也没法在这台机器上测。
- **Elasticsearch/真实 Neo4j 这次会话验不了**：docker daemon 没起（`docker ps` 连不
  失败），按 ADR-0006 先例处理——`Bm25Memory`/`GraphMemory` 两个离线 Fake 承担 CI
  回归职责，`evals/run_hybrid_rag.py --mode real` 里 ES/Neo4j 那两段留给用户自己起
  `docker compose up -d elasticsearch`（Neo4j 服务已存在）后手动跑。
- **`--mode offline` 全链路已验证**：`Bm25Memory`（真 BM25 算法）+ `SqliteMemory`
  （向量腿离线替身）+ `GraphMemory`（图腿）组装出的 `HybridGraphRAG`，两条核心断言
  都过——RRF 融合排序正确（同时命中两路的内容排名高于只命中一路的）、多跳召回
  确实生效（查询词能通过两跳图扩展找到原文里完全没出现过的关联事实）。
- 实体识别（`_seed_entities`）用简单子串包含判断查询文本里是否出现已知实体名，
  没有真正的实体链接（entity linking），同名实体歧义、别名/同义词都处理不了；
  出现歧义问题或需要模糊匹配时再引入专门的实体链接层。
- 内核对本次改动零感知：只新增 adapter 文件、一个融合模块、一个 eval 脚本，未触碰
  `kernel.py`/`ports.py`，验证了 ADR-0001 的扩展性承诺。
