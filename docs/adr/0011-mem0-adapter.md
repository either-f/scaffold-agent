# ADR-0011: mem0 记忆 adapter，跟 CompositeMemory 二选一

日期: 2026-08-10  状态: 已采纳

## 决策

- 新增 `Mem0Memory`（`adapters/memory/mem0_adapter.py`），实现 `MemoryPort.add/search`，
  底层是真实 `mem0ai`（2.x，PyPI 现行版本，单次 LLM 抽取 + 语义/BM25/实体三信号混合检索）。
- 组件选型：LLM 用 mem0 官方 `deepseek` provider（复用项目已有 `DEEPSEEK_API_KEY`）；
  embedder 用 `openai` provider 指向 DashScope 的 OpenAI 兼容端点
  （`https://dashscope.aliyuncs.com/compatible-mode/v1`，复用已有 `DASHSCOPE_API_KEY`，
  模型 `text-embedding-v4`/1024 维，跟 `PgVectorMemory` 保持一致）——mem0 Python embedder
  provider 列表不含 `litellm`/`deepseek`，DashScope 官方提供 OpenAI 兼容模式，走这条路线
  零新增 provider 集成成本；vector store 用本地 `chroma`（跟项目已有 sqlite checkpoint、
  effects db 一样落本地文件，不额外起基础设施）。
- **不塞进 `CompositeMemory`**：`CompositeMemory` 手动维护 episodic/semantic 分层 +
  独立离线巩固脚本（`evals/run_consolidation.py`）做"事实/偏好抽取"；mem0 的 `add()`
  内部本来就是单次 LLM 调用做等价的抽取+去重。两者硬塞在一起等于同一件事做两遍，
  职责重复。`Mem0Memory` 定位为跟"`CompositeMemory` + 巩固脚本"整套记忆栈平级的
  **替代选项**，接哪个由使用方在组装 `AgentKernel` 时选 adapter 决定，不是组合关系。
- `preferences`（`ReactPlanner` 固定 query 注入路径，见 ADR-0005/0009 附近的偏好记忆扩展）
  在 mem0 里没有直接对应机制：mem0 的检索永远按 query 相关性走。若要在 `Mem0Memory` 上
  复刻"无条件注入"效果，需要业务层自己固定一个 query 或用 `metadata` 打标后单独查询，
  本次不做，selecting mem0 隐含放弃这条能力，用回 `CompositeMemory` 才有。
- 依赖声明为可选 extra `mem0 = ["mem0ai>=2.0,<3"]`；新增 `evals/run_mem0.py` 验证真实
  add→search 往返（本地临时目录，跑完即删，不依赖常驻服务）。

## 原因

mem0（GitHub 6.3 万星，同类记忆项目最热）此前只在 `MemoryPort` 接口设计里借了
"add/search" 的形态（见 `ports.py` 里 `MemoryPort` 的 docstring），没有真实包接入过。
作为通用脚手架，"记忆层能不能换成市面最主流的现成方案"是验证 `MemoryPort` 扩展性
和简历辨识度的直接项目，符合融合纪律"能当依赖直接用的就直接用"。

## 后果与迁移条件

- CI 离线门禁不跑 `run_mem0.py`（需要真实 DeepSeek + DashScope key），职责保持跟
  `run_graph.py`、`run_worker_real.py` 一致：真实凭据场景手动/CD 环境跑。
- Chroma `collection_name` 直接用调用方传入的 `namespace`，Chroma 对合法字符有限制
  （3–63 位，字母数字/下划线/连字符，首尾须字母数字）；当前留一条 ponytail 注释，
  真要支持任意 namespace 字符串时在 adapter 里加一层哈希化命名，本次不做。
- mem0 2.x 起图记忆能力已从开源版移除、收进 Mem0 Platform 付费功能，因此
  `Mem0Memory` 不覆盖图记忆场景——项目里图记忆继续由 `Neo4jGraphMemory`（ADR-0006）承担，
  两者不冲突，一个是原文事实记忆二选一，一个是图记忆。
- 内核对本次改动零感知：只新增一个 adapter 文件、一个 extra、一个 eval 脚本，
  未触碰 `kernel.py` / `ports.py`。
