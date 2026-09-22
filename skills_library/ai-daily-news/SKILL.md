---
name: ai-daily-news
description: 需要整理当日 AI 相关新闻与关键人物动态、产出每日摘要时使用本技能
---

# AI Daily News Skill

## 流程
1. 并行调用已注册的采集 adapter（`aihot`：AI 热点新闻聚合；`x_kol`：追踪 claude/openai/google
   等关键人物在 X 上的动态，含模型发布/额度重置等信号）。
2. 过滤：只保留跟模型/公司动态相关的条目（`x_kol` 已在 `structured.is_model_update`
   标出；`aihot` 条目按标题/摘要关键词二次过滤）。
3. 跨源去重合并：同一新闻常被多个源报道，按标题做纯 stdlib 字符串相似度判重
   （如 2-gram Jaccard，参照仓库 `adapters/memory/consolidation` 已有的判重思路，
   不引入新依赖），命中則合并为一条、来源列表拼接。
4. 入库：`SqliteContentStore.upsert(module="ai-daily-news", item)`，去重键
   `(module, source_platform, external_id)` 已在存储层保证，这一步只处理跨平台的
   语义重复，不重复造轮子。
5. 生成每日摘要：对当日新入库条目做摘要，按"模型发布 / 额度与限流 / 公司动态 /
   其它"分类输出，只读操作，幂等，无需审批。

## 约束
- `x_kol` 依赖付费 X API Bearer Token（环境变量 `X_BEARER_TOKEN`），未配置时该源
  会报错，流程需能在缺失该源时仍正常产出 `aihot` 单源日报，不整体失败。
- 摘要只做信息整理，不做投资建议或未经证实的传闻断言，不确定信息标注来源存疑。
- 本模块没有非幂等风险动作（不发送、不投递），不需要走 HITL 审批。
