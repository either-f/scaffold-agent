---
name: github-trending
description: 需要生成"每日 GitHub 项目推荐"时使用本技能，抓取当日趋势榜单并给出推荐理由
---

# GitHub Trending Skill

## 流程
1. 调用 `GhfindScraper.fetch({"language": ..., "since": "daily"})` 抓当日榜单（可不带 language 抓全站，也可分语言多次抓）。
2. 每条结果用 `SqliteContentStore.upsert("github-trending", item)` 入库，去重键是 (module, source_platform, external_id)——同一仓库当天重复抓取不会产生重复条目，只会更新 star 数需要单独走 upsert 之外的更新逻辑（当前版本不覆盖已存在行，见约束）。
3. 对每条新入库的条目，用模型生成一句"推荐理由"：这个项目是什么、为什么今天值得关注（结合 today_stars 增速、description、language）。这一步是纯只读文本生成，幂等，不需要审批。
4. 按 `today_stars` 排序输出，可选按 language 分组。

## 约束
- 这是唯一读三个模块里"零风险动作"的模块：没有自动 star/自动 fork/自动 PR 之类的操作，全程只读，不接 HITL 审批（`ModuleSpec.has_approval=False`）。
- `upsert` 对已存在的 (module, source_platform, external_id) 直接跳过（INSERT OR IGNORE），不会更新 star 数——当前设计目标是"发现新项目"，不是"追踪某个项目的 star 曲线"；要做后者需要新增一个允许更新 structured 字段的写路径，先不做（YAGNI）。
- GitHub trending 页面结构可能改版，解析失败时应该整批跳过并记录错误，不要让单条解析异常拖垮整个 fetch。
