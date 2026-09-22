---
name: job-hunter
description: 需要多平台抓取招聘岗位/内推文章、按用户条件筛选并准备投递沟通材料时使用本技能
---

# Job Hunter Skill

## 流程
1. 从 module_registry 读取本模块绑定的 scrapers（boss/zhilian/zhihu/xhs/douyin），
   按用户设定的关键词/城市/薪资范围并行调用各自 `fetch(query)`。单个平台失败不阻塞其它平台。
2. 岗位类结果（boss/zhilian）与内推文章类结果（xhs/zhihu/douyin）分别处理：
   - 岗位：按薪资范围、城市、关键词命中度过滤。
   - 内推文章：按提到的目标公司/岗位关键词过滤。
3. 用 `SqliteContentStore.upsert(module="job-hunter", item)` 去重入库，已存在的
   （external_id 命中）直接跳过，不重复处理。
4. 对通过筛选的新条目，生成沟通话术草稿 / 简历匹配理由（只读、幂等，可放心多次生成）。
5. **自动投递、主动私信/评论区留言等有账号侧副作用的动作，禁止在本技能流程内直接执行**，
   只能产出待审批的动作草稿（目标 URL、话术文本），交给 HITL 审批通过后才允许调用对应
   非幂等工具。

## 约束
- 每次运行处理条目数设上限（如单平台单次 50 条），避免高频请求触发平台风控。
- 抓取失败（反爬拦截、登录态失效）要显式记录失败原因，不能静默丢弃，前端需要能看到
  "该平台本次未抓到数据"而不是误以为没有新岗位。
- 涉及自动投递/自动沟通的工具必须声明 `ToolEffectPolicy(idempotent=False)`，走
  EffectLedger，防止恢复/重试导致重复投递同一岗位。
