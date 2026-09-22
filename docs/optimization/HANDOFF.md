# Scaffold Agent 优化任务交接

更新时间：2026-09-22（Asia/Shanghai）

这是一项已经授权实施的跨层优化任务。目标是把项目整理成“个人情报与行动工作台”：发现岗位、开源项目和 AI 资讯，查看有来源的详情，按用户保存/关注/稍后阅读，并把岗位加入个人机会、记录申请阶段和下一步。真实模式、演示模式和旧入口要明确隔离。

## 交接给下一位 agent 的总提示

工作目录：`G:\demo\fuyong\demo\scaffold-agent`

请先阅读：

1. 本文件；
2. `docs/optimization/IMPLEMENTATION.md`；
3. 规划原件 `%USERPROFILE%\.codex\visualizations\2026\09\08\01a07e91-34a0-7161-928d-d47c5342eee3\scaffold-agent-optimization-plan.md`；
4. 当前 `git status --short` 和相关文件实际内容。

不要根据旧 agent 的口头报告判断完成度，以当前工作树和实际请求结果为准。工作区有大量未跟踪文件，属于本任务或用户已有变更；不要 reset、checkout、清理未跟踪文件，也不要提交或 push。

## 固定分工与边界

- 后端规格：`gpt-5.6-luna / xhigh`。范围是 API、SQLite、采集、鉴权、调度、运行记录。
- 前端规格：`gpt-6-astra / high`。范围是 React/Vite 页面、交互、CSS、模拟模式和真实联调。
- 主 agent 负责契约冻结、跨层审阅、真实 HTTP/浏览器验收和文档。
- 已禁用的 frontend/design、Figma、lark 技能不能使用。
- 复用当前 React/Vite/CSS、FastAPI/SQLite、APScheduler 3.x 和已有 scraper；不要迁移 Tailwind/shadcn、不要新增状态缓存层、不要自建队列或第二套调度器。
- 除用户明确要求外，不新增测试文件；优先使用已有 pytest、真实隔离 HTTP、Playwright CLI 和 build/lint 验收。
- 不要使用真实 Cookie、验证码、账号密码或发送邮件/外部投递。采集失败信息必须脱敏。

## 已完成并有证据的部分

### 启动、模式和鉴权（B01/B02 的主要部分）

- `run_server.py` 已改成延迟构建 app，默认 `APP_MODE=real`；demo 需显式 `APP_MODE=demo`。
- real 默认使用 `runs/`，demo 使用 `runs/demo/`，real 空库不会自动 seed demo 数据。
- real 要求 `JWT_KEY` 至少 32 字符；`PLATFORM_OWNER_ID` 指定全局 owner；`ENABLE_SCHEDULER=1` 才开启周期采集。
- real 没有邮件发送器时，`GET /public/ask-code` 返回业务码 `1013`，不回显验证码、不打印验证码；已有账户仍可登录。
- 全局采集、规则、来源和内容审批写操作需要 owner；个人收藏、关注、阅读和机会属于登录用户。

### 来源、去重和后台采集（B03-B06）

主要文件：

- `src/agent_kernel/adapters/platform_store.py`
- `src/agent_kernel/adapters/platform_collectors.py`
- `src/agent_kernel/adapters/api/collection_runtime.py`
- `src/agent_kernel/adapters/api/platform.py`
- `src/agent_kernel/adapters/api/app.py`

当前接入的公开采集器只有：

- `github-trending`：GitHub Trending Python 日榜；
- `ai-daily-news`：V2EX 热门话题，经 AI 关键词筛选；
- `job-hunter`：V2EX 酷工作公开页面。

registry 中声明的其他 scraper 不等于已经接入平台，不能自动启用。

已实现：来源平台、外部 ID、来源 URL、发布时间、抓取时间和 origin；来源身份 upsert；新增/更新/跳过计数；`queued/running/succeeded/partial/failed/interrupted` 运行状态；活动 run 去重；worker 独立连接；重启中断恢复；异常脱敏；保守城市识别；旧数据保留。

新接口：

- `POST /api/platform/collection-runs`，body `{ "module_id": "job-hunter" }`，返回 202 和 `run_id`；
- `GET /api/platform/collection-runs/{run_id}`；
- `GET /api/platform/collection-runs?module_id=&limit=20`；
- 旧 `POST /api/platform/collect/{module_id}` 保留兼容，但 real 失败返回 502，活动任务返回 409。

### 个人岗位、动作和申请（B07-B10）

主要文件：

- `src/agent_kernel/adapters/personal_store.py`
- `src/agent_kernel/adapters/api/personal.py`

已实现并在隔离真实 HTTP 中验证：

- 三类实体详情按 `kind + id` 查询：`job | project | news`；不存在或不可见返回 404；
- 手动岗位 POST/PATCH；`origin=manual`、`created_by` 私有隔离；非法 URL、显式 null、错误类型被拒绝；
- `GET /api/platform/my/items` 按用户/类型/动作分页；
- `PUT /api/platform/my/items/{kind}/{id}/actions` 显式设置布尔状态，重复请求幂等；
- `GET /api/platform/applications`；
- `PUT /api/platform/jobs/{id}/application`，支持阶段、下一步、时间、笔记、完成状态和历史；
- `applied` 只表示用户记录投递，不表示外站提交；旧 applied 动作兼容；
- 私人接口带 `Cache-Control: private, no-store` 和 `Vary: Authorization`。

隔离 HTTP 验收曾验证：匿名 401、普通用户访问 owner 管理接口 403、越权目标 404、双账户隔离、动作显式幂等、申请更新不重复写历史、重启后数据库数据仍保留。

### 演示前端 F01-F08 的主要成果

演示入口：`http://127.0.0.1:5173/?demo=1#/today`（端口以实际启动参数为准）。

主要文件：

- `frontend/src/components/DemoWorkspace.tsx`
- `frontend/src/components/DemoDialog.tsx`
- `frontend/src/components/DemoSettings.tsx`
- `frontend/src/demo.ts`
- `frontend/src/index.css`

已有五入口：今天、发现、我的行动、自动化、连接设置；旧 hash 有兼容映射。演示数据只在当前页面内存中，不请求 API，不访问外站，刷新后重置，并明确显示演示边界。

已经检查过 390、768、1024、1440 四档宽度：演示五入口无横向溢出，窄屏搜索可见。详情使用原生 `dialog`，有取消/Esc/返回触发按钮的处理；岗位、项目和资讯使用 `kind + id`，动作状态分开；自动化和连接有 loading/empty/error/stale/unauthorized/success 等演示状态；“申请”文案没有伪装成真实外站投递。

## 已完成的联调与继续边界

### 真实前端联调 I01/I02：已完成

真实工作台文件：

- `frontend/src/components/Workspace.tsx`
- `frontend/src/components/WorkspaceDetail.tsx`
- `frontend/src/components/CollectionStatus.tsx`
- `frontend/src/collection.ts`
- `frontend/src/workspace.ts`
- `frontend/src/api.ts`

2026-09-10 下午的隔离浏览器验收已覆盖以下路径，详见 `IMPLEMENTATION.md` 的验收记录与截图：

1. 创建手动岗位 → 详情 → 收藏/稍后阅读 → 机会阶段与下一步 → 刷新及后端重启后仍保留；
2. `reviewer` 与 `second` 账户切换无个人记录残留，普通账户的管理操作明确显示 403；
3. 新 202 采集接口及六种运行状态；终态停止轮询；运行尚未完成时离页，12 秒内运行查询增量为 0；
4. 真实工作台未知来源、时间和评分不伪装为成功或精确评分；
5. 五入口在 390/768/1024/1440 四档宽度无横向溢出；独立 `?demo=1` 无业务请求。

默认 `/` 已是新工作台，`?workspace=1` 是兼容入口，旧版通过 `?legacy=1` 访问。
前端 agent 曾因额度中断是历史事件，不再代表当前联调未完成。不要重新实现上述已有功能。
上述采集异常态使用受控 fixture 验证真实解析、运行与落库链路，不等同于外网长期稳定性验收。

### 后端已有边界

- 失效保存项保留 `available=false` / `unavailable_reason`，不会静默消失，也不泄露他人手动岗位；
- 三类真实详情中的未知匹配/评分/重要度为 JSON `null`；
- 聚合和个人接口使用 `Cache-Control: private, no-store`，`Vary` 保留 Authorization 与既有字段；
- 无需另建删除/回收系统、缓存层或新的用户动作表。

评分聚合契约的本轮收尾结果见 `IMPLEMENTATION.md` 最后的记录；未知值必须在接口与页面两端保持一致。

### 明确延后，不要误当成当前 bug

以下功能按计划刻意保留为演示或后续项，不要在本轮擅自扩张：

- 自动化规则新增/编辑真实 CRUD；
- 数据源新增、OAuth/Cookie/真实连接配置；
- AI 日报真实生成、邮件/飞书/微信推送；
- 招聘网站自动投递；
- 团队/RBAC/多租户、分布式 worker；
- 全文知识库、RAG 重构、通用流程画布；
- Excel 批量导入、简历存储、日历同步。

真实自动化页应明确写“模板编辑尚未接入真实保存”，并提供独立演示入口；不能显示“已发送/已连接/已采集成功”这样的伪成功。

## 验证命令

在项目根目录执行：

```powershell
Set-Location 'G:\demo\fuyong\demo\scaffold-agent'
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest tests/ -q -rs
.\.venv\Scripts\python.exe evals/check_core_imports.py
```

在前端目录执行：

```powershell
Set-Location 'G:\demo\fuyong\demo\scaffold-agent\frontend'
npm run build
npm run lint
```

2026-09-10 晚间状态核查的全量结果：`158 passed, 4 skipped, 6 warnings`；core import 门禁、frontend build/lint 通过。4 个跳过项因缺少可选依赖 litellm（2 项）、flashrank、daytona；lint 有 3 条既有 Fast Refresh 警告。后续每轮修改仍应重新验证并记录实际结果，不能沿用旧数字充当新验收。

2026-09-22 重跑同样四条命令，结果一致：`158 passed, 4 skipped, 6 warnings`、core import exit 0、build 成功、lint 仅 3 条既有 Fast Refresh 警告。

## 隔离验收约定

之前使用过的隔离数据库目录形如：`.cache/browser-acceptance-*`。不要操作用户正在使用的 `runs/*.db`。

2026-09-22 本轮隔离服务用 `8032`/`5182`，验收后已关闭。历史快照：2026-09-10 晚间 `8010`/`5173`、`8021`/`5181` 有监听，`8012`/`5174` 没有监听；这些端口可能仍有其他 agent 进程，启动前重新检查，不要批量 kill。
此前“8010 一直在跑改造前代码”的说明已经过期，晚间实际响应包含 real 标记与 no-store 头。
端口不是代码版本证据，Python 非热重载进程也不会自动加载后续修改；验收新改动应另起隔离服务。
不要批量终止 Python/Node，也不要把这些端口快照当作未来仍成立的环境配置。

若需要重新做浏览器验收：

1. 新建项目内 `.cache` 临时数据库和随机 32 字符 JWT；
2. 使用两个隔离账号，例如 `reviewer`、`second`，密码仅用于本次本地验收；
3. 把采集函数在内存中替换为受控 fixture，不要依赖外网；
4. 前端用 `VITE_API_BASE` 指向该隔离服务；
5. 使用 Playwright CLI，不新增 Playwright 测试文件；截图写入 `output/playwright/`，并实际查看代表性截图。

## 当前工作区和风险

- `.github/workflows/ci.yml`、`README.md`、`pyproject.toml`、`uv.lock` 已修改；大量 `src/`、`frontend/`、`tests/`、配置和文档是未跟踪文件。
- pytest 通过与浏览器验收是不同证据；I01/I02 的依据是上述实际浏览器记录，而不是测试数量。
- 不要把 demo 截图解释为真实数据源、邮件或外站投递证据。
- 不要因为 `AUTO_LOGS`、`SOURCE_METRICS` 等常量存在于后端文件就直接删掉 demo 分支；只确认 real 分支不读这些常量。
- `frontend/src/pages/Automation.tsx` 和 `Sources.tsx` 复用旧页面组件，已接入真实 API/collection hook；配置表单仍明确跳转演示。
- 旧版招聘/项目/AI 日报的固定汇总数字、通知红点 6 和日报推送假开关已于 2026-09-22 清除，改为当前筛选结果的真实计数（匿名显示「需登录」），旧版 390px 横向溢出已修复，详见 `IMPLEMENTATION.md` 的「2026-09-22 S01/S02/S03 验收结果」。legacy 的自动化规则、数据源配置和日报仍是明确标注的原型，未接入真实保存。
- 无需 git commit/push。最终交付要说明实际改动文件、命令结果、浏览器路径、截图和剩余延后项。

## 最短继续顺序

1. 先读 `IMPLEMENTATION.md` 最新记录与当前代码，避免重复实现 I01/I02；
2. 未知评分契约与 legacy 去假数据/窄屏收尾已完成（2026-09-22）；剩余明确遗留是远程 CI 未验证，以及自动化规则 CRUD、数据源配置/OAuth、日报生成与通知发送；
3. 按本轮实际改动做既有回归与隔离 HTTP/浏览器验证，不修改用户主库；
4. 扩展规则配置、日报或通知前，先确定下一轮功能范围，不把演示标记移除当作实现；
5. 更新证据、剩余项及服务清理情况，不自动提交或推送。
