# 优化实施记录

2026-09-08，用户已授权实施优化计划。后端由 GPT-5.6 Luna / xhigh 执行；前端由 GPT-6 / high 设计和实现。主任务负责契约、审阅与真实验收。

## 基线

- 计划原件：`%USERPROFILE%\.codex\visualizations\2026\09\08\01a07e91-34a0-7161-928d-d47c5342eee3\scaffold-agent-optimization-plan.md`。
- 修改前源码备份：项目内 `.cache/optimization-baseline-20260908-130944/`，含未跟踪的前后端源码、启动脚本、依赖说明、README 和 CI。
- 不覆盖用户现有 `runs/*.db`，运行检查用隔离目录。无自动提交、推送或外部发送。
- 复用 React/Vite/CSS、原生 HTML、FastAPI/SQLite、APScheduler 3.x 和当前 GitHub Trending/V2EX 采集入口。

## C01 固定契约

- 实体引用为 `kind + id`，kind 为 `job | project | news`。不同类型可以有相同数字 ID。
- 来源字段：`source_id`、`external_id`、`source_url`、`published_at`、`fetched_at`、`origin`；origin 为 `real | demo | manual | unknown`。未知时间和评分保留 null。
- 用户动作：收藏 favorite、关注 watch、稍后读 read_later、完成阅读 read 分开；收藏不自动创建待办。
- 机会阶段：`saved | preparing | applied | interviewing | offer | rejected | archived`。applied 表示用户记录投递，不表示系统完成外站提交。
- 运行状态：`queued | running | succeeded | partial | failed | interrupted`；结果保留 inserted/updated/skipped 和脱敏 error。
- 新独立前端演示模式用 `?demo=1` 显式打开，不挂载认证或业务 API 请求；真实模式保留现有访问方式。
- 五入口：今天、发现（岗位/项目/AI）、我的行动、自动化、连接设置。旧 home/recruitment/projects/ai-daily/automation/sources hash 继续有对应入口。
- `APP_MODE=demo|real`，real 是后端启动默认。demo 使用独立数据库并显式 seed，现有用户数据库不能自动灌演示数据。
- real 模式签名密钥由 `JWT_KEY` 配置；全局管理由 `PLATFORM_OWNER_ID` 指定 owner，普通用户仅管理个人动作和机会。
- `ENABLE_SCHEDULER=1` 显式开启周期采集；默认不自动网络采集，手动触发由 owner 发起。
- 新运行接口：`POST /api/platform/collection-runs`（module_id）和 `GET /api/platform/collection-runs/{run_id}`。旧 collect 响应保留兼容。
- 详情 GET `/api/platform/jobs/{id}`、`projects/{id}`、`news/{id}`；手动岗位 POST `/api/platform/jobs`、PATCH `/api/platform/jobs/{id}` 已实现。
- 个人 GET `/api/platform/my/items`、GET `/api/platform/applications`、PUT `/api/platform/jobs/{id}/application` 已实现；真实工作台已开始接入。
- 旧 auth ResponseResult 与平台原字段保持兼容，新字段增量添加。自动化规则/来源配置 CRUD、日报生成和通知发送仍保留为明确的演示/后续边界。

## 状态

| 范围 | 状态 | 证据 |
| --- | --- | --- |
| C01 基线/契约 | 完成 | 上述源码备份、字段与职责约定 |
| F01–F04 前端 | 已实施 | `frontend/src/components/DemoWorkspace.tsx`，`?demo=1` 可访问 |
| F05–F08 前端 | 已实施、模式与宽度已验收 | `DemoWorkspace.tsx`、`DemoSettings.tsx`；配置与日报仍为明确演示，不能当作真实发送能力 |
| B01 后端 | 已实施 | `run_server.py`、`AuthSettings`、验证码未配置保护 |
| B02 后端 | 已实施、隔离验收通过 | owner 权限、双账户隔离、失效保存项和私有缓存边界已复核 |
| B03 后端 | 已实施、采集/去重验收通过 | 三个公开来源按 ID/URL 去重、无随机匹配分；不是其他 registry scraper 或外网稳定性的验收 |
| B04–B06 后端 | 已实施、六态联调通过 | 后台运行、失败/部分失败/重启中断、来源状态与轮询停止，详见下文 |
| B07–B10 后端 | 已实施、闭环验收通过 | 详情、手动岗位、个人动作、机会阶段与历史，含刷新及重启后的持久化 |
| B11 后端 | 本地门禁通过、远程待验证 | CI 配置已增加 API 回归和 frontend build/lint；未推送、远程 CI 未运行 |
| I01 联调 | 完成 | 2026-09-10 隔离浏览器验收通过，见下「2026-09-10 验收记录」 |
| I02 联调 | 完成 | 2026-09-10 隔离浏览器验收通过，含 202 触发、状态展示与轮询停止 |

## 验收

既有 pytest、core import 门禁、npm build/lint，加隔离数据库的真实接口和 Playwright 浏览器操作。不新增测试代码。未运行的检查不记为通过。

## 2026-09-10 验收记录

隔离环境：`.cache/browser-acceptance-opus01/`（独立 sqlite，未触碰 `runs/*.db`），API `127.0.0.1:8021`，
前端 `localhost:5181`（`VITE_API_BASE` 指向 8021）。`APP_MODE=real`、随机 43 字符 `JWT_KEY`、
`ENABLE_SCHEDULER=0`、`PLATFORM_OWNER_ID=1`。采集的 `_fetch_html`/`_fetch_json` 在内存中替换为受控
fixture，全程不打外网；解析、去重、run 状态与落库仍走真实代码路径。两个隔离账号：`reviewer`（owner）、
`second`（普通用户）。

验收当时的历史端口快照：`8012`/`5174` 尚有上一轮 python/node 监听，因此选用 8021/5181。
晚间复核时 `8012`/`5174` 已无监听；端口使用情况应在每次启动前重新检查，不能沿用历史快照。

### 自动化回归

- `pytest tests/` → `158 passed, 4 skipped, 6 warnings`
- `evals/check_core_imports.py` → exit 0
- `npm run build` → 成功；`npm run lint` → 通过（3 条既有 fast-refresh warning）

### 隔离 HTTP 契约验收（32/33）

openapi 暴露新端点；匿名 401；非 owner 触发采集 403；owner 触发 202 并到达 `succeeded`（`inserted=3`）；
三个模块采集均成功；`origin=real`；real 详情 `match`/`score` 为 JSON `null`；私有接口 `Cache-Control:
private, no-store`；手动岗位 `origin=manual`；动作显式幂等；机会阶段 `preparing → applied`；
第二账户对他人手动岗位 404、个人列表与机会列表均为空。唯一 FAIL 是验收脚本自身写错了路径模板名
（`/api/platform/jobs/{item_id}`），详情端点本身可用。

### I01 浏览器闭环（reviewer）

未登录显示"登录后查看个人收藏和机会"，无假数据 → 登录 → UI 创建手动岗位 → 详情弹窗自动打开 →
收藏 + 稍后阅读 → 机会阶段设为"面试中"、下一步"准备系统设计题" → 卡片与 KPI 同步 → 刷新仍在 →
**重启后端**后数据完整存活（`stage=interviewing`/`applied`、`next_step` 均在）。

详情弹窗对未知值的显示：`发布时间：未提供 · 抓取时间：未提供`、`来源类别：手动记录 · 评估：暂无可核验评分`，
并显式声明"阶段表示你记录的申请进度，不会向外部网站提交简历"。

### 双账户隔离（浏览器）

`reviewer` 退出后页面无残留 → `second` 登录后三项 KPI 均为 0、列表"暂无内容"；`second` 打开自动化页
得到诚实的 `权限提示：权限不足（403）`，而不是空白或伪成功。

### I02 采集运行（浏览器）

owner 自动化页统计来自真实 run（已启用规则 3 / 最近记录 4 / 最近成功 4 / 最近失败 0，并注明"调度器未启用"）；
点击"手动运行一次"经 202 端点触发，"最近执行"列表出现新记录 `job-hunter · 成功 / succeeded · 新增 0 / 更新 0`
（新增 0 是 fixture 去重后的真实结果）；静置 8 秒后端 `collection-runs` 请求增量为 **0**，确认终态后停止轮询。

连接设置页显示真实的 3 个已接入来源，`0 个状态未知，不代表健康`、`今日采集 未知（未计量时显示未知）`；
旧的 `SOURCE_METRICS`/`AUTO_PENDING`/`AUTO_CHANNELS` 演示常量未泄漏到 real 分支。

### I02 六种运行状态逐一验证（第二轮补验）

第一轮只观察到 `succeeded`——fixture 15ms 就跑完，中间态与异常态没有机会出现，
即「失败不能先显示成功」这条最关键的反伪成功检查当时**并未真正验过**。
为此给验收 fixture 加了运行时可切换开关（`<隔离目录>/fixture_mode`，值 `ok|fail|slow|partial`，
每次抓取时读取，不必重启），逐一构造：

| 状态 | 构造方式 | 后端结果 | 界面显示 |
| --- | --- | --- | --- |
| `queued` | 触发响应本身 | `POST` 返回 `{"status":"queued"}` | `RUN_LABELS` 同一映射渲染 |
| `running` | `slow` 模式（抓取 sleep 45s） | 运行中 | `job-hunter · 正在运行` + 运行编号 + 「离开页面或隐藏标签会停止查询，不会取消后端任务」；规则按钮变 `执行中…` 且禁用，其余规则按钮同时禁用 |
| `succeeded` | `ok` 模式 | `succeeded` | `job-hunter · 成功 / succeeded · 新增 0 / 更新 0` |
| `partial` | `partial` 模式：patch `PlatformStore._upsert_job_result`，让 3 条岗位中含「全栈」的那条写库抛错 | `partial`，`error='条目保存失败: 1'`，`skipped=2` | `job-hunter · 部分失败 / 条目保存失败: 1` |
| `failed` | `fail` 模式（抓取抛 `ConnectionError`） | `failed`，`error='采集失败: CollectionError'`（脱敏，无 URL/堆栈） | `job-hunter · 失败 / 采集失败: CollectionError` |
| `interrupted` | 运行中途 `Stop-Process` 强杀后端再启动 | `interrupted`，**不会卡在 running** | `job-hunter · 已中断 / interrupted` |

失败发生后，统计卡如实变为 `最近记录 10 / 最近成功 7 / 最近失败 3（含部分失败与中断）`，
来源页对应来源转为 `最近失败`、操作按钮变 `重试采集`，没有粉饰成健康。

「离页停止轮询」也重新做了严格版：`slow` 模式触发后离开自动化页，12 秒内后端 `collection-runs`
请求增量为 **0**，且该 run 在测量窗口结束时仍处于 `running`——排除了「因为跑完才停」的干扰。

小瑕疵（未改）：运行记录副标题统一以原始枚举开头（`succeeded · …`、`interrupted`），
主标题已是中文状态，副标题作为技术细节行可接受，改动收益不大。

### 模式隔离与宽度

`?demo=1` 停留 5 秒，后端日志增量 **0** 行，确认演示模式不发业务请求；`?legacy=1` 旧入口仍可加载。
四档宽度（390/768/1024/1440）在 today/discover/actions/automation/connections 五个入口
`scrollWidth - clientWidth` 均为 0，无横向溢出。

### 本轮修复（均由浏览器验收发现）

1. `frontend/src/demo.ts` 新增 `formatDateTime()`：后端 `run_time()` 返回的 UTC ISO 串
   （`2026-09-10T08:41:21.753678+00:00`）此前被前端原样渲染。改为 `9月10日 16:41`；对后端可能直接返回的
   `尚未运行` 这类文案做原样透传。
2. `pages/Automation.tsx`、`pages/Sources.tsx` 使用该格式化函数——超长时间戳此前挤占列宽，导致
   `job-hunter · 成功` 与 `最近成功` 被逐字折行。
3. `index.css` `.add-card` 补 `display: block; box-sizing: border-box;`——它是 `<a>`，此前是内联盒，
   垂直 padding 不撑行高，按钮与"添加数据源"说明文字重叠。
4. `index.css` `.action-icon` 改为 `flex` + `gap`——操作列两个按钮此前无间隔，连读成"手动采集查看记录"。

修复后重新截图复验，四处均正常。截图：`output/playwright/optimization/`
（`real-*.png` 为修复前，`fixed-*.png` 为修复后）。

## 2026-09-10 旧版入口（legacy）去假数据

用户反馈旧版首页视觉更好，但那一版仍在展示假数据。保留旧版视觉，只把不诚实的部分改掉：

**数据层（`runs/platform.db`，改前已备份为 `runs/platform.db.bak-20260910-173129`）**

- 删掉 9-06 旧采集器写入的 34 行 `origin='unknown'` 内容（jobs 8 / projects 21 / news 5）。
  这批行的 `match` 是旧代码 `random.randint(78, 92)` 摇出来的随机数，却以「AI 匹配度 92%」的措辞展示；
  同时没有 `external_id`/`source_url`，无法核验来源。
- 清空 `todos`（3 行）与 `ranks`（5 行）——`seed_demo()` 灌的固定文案，其中「Boss 直聘 · 新消息 ·
  字节跳动 2轮技术面试邀请」指向一个并不存在的 Boss 直聘对接。
- 用当前代码重采三个模块：jobs 8 / projects 16 / news 3，全部 `origin='real'`、带 `source_url`、
  `match=0`（不再编造）、`reason=''`（不再编造推荐理由）。

**前端（`pages/Home.tsx`、`index.css`）**

- 「查看全部（{data.todos.length + 4}）」的硬编码 `+ 4` 去掉——这是个凭空多出来的计数。
- 「今日热榜」「待处理」为空时给出空状态文案，不再渲染成空白卡片。
- 「推荐理由」区块仅在 `reason` 非空时渲染，避免出现只有标题没有内容的空块。

**后端（`api/platform.py`）**

- 新增 `SOURCE_LABELS` 与 `entity_meta()`：当前采集器写入的 `meta` 为空串，导致卡片副标题整行消失。
  改为按行上真实存在的字段拼装（来源展示名 · 城市 · full_name · 语言），缺哪项就不写哪项，
  不补任何推断值。`/home` 与 `/jobs` 共用。

改后回归：`158 passed, 4 skipped`、core import 门禁 exit 0、`npm run build` + `npm run lint` 通过。
截图 `output/playwright/optimization/legacy-honest-1440.png`。

遗留的视觉稀疏不是缺陷：V2EX 招聘帖本身只有标题和（有时）城市，没有公司、薪资、描述，
所以岗位卡确实比项目卡空——这是真实数据的信息量，不应该用推断值填满。

### 未知评分契约：2026-09-10 晚间已修复

`api/platform.py` 三类真实聚合列表的 `match` / `score` 已改为 JSON `null`，与详情一致；demo 原有字符串格式不变。
前端类型、卡片、汇总与预览已同步：缺值显示“未知”，全未知不冒充高分数量 0；真实 `0%` 保留。
旧首页四处实体预览显式传 `score: null`，不再触发示例高分；独立原型默认分数标记“演示”，固定评语不再声称真实评估。

当晚隔离 HTTP 验证真实三类列表/详情 null、demo 原格式和 no-store；浏览器验证三类列表/预览、首页三个类型、
混合 null/90%/0% 汇总及独立 demo 零业务请求。截图 `output/playwright/optimization/score-contract-home-preview-1440.png`。
2026-09-22 状态复核重跑：`158 passed, 4 skipped, 6 warnings`、core import、build/lint 通过（3 条既有 lint 警告）。
旧版招聘 390px 页面仍有约 85px 横向溢出，不能用新工作台四档宽度通过代替旧版验收。

## 2026-09-22 旧版收尾规格

目标：保留既有视觉，修复窄屏溢出、移除伪统计并明确原型边界；不补日报、通知、OAuth 或外站投递 API。
未知分数、零值、真实写操作与 demo 隔离保持原契约；不改用户数据库、不新增依赖/测试文件、不提交或推送。

### S01：旧版数据与原型标识（M）

- **证据 / 范围**：`Recruitment.tsx` 固定 7/3、筛选计数；`Projects.tsx` 固定 21/15、默认过滤；
  `AiDaily.tsx` 固定模型更新 4、本地推送开关；`Topbar.tsx` 通知 6；`App.tsx`、`DesignPreview.tsx`、`cards.tsx` 的共用展示边界。
- **依赖**：已完成的评分契约；与 S02 的 CSS 文件无写冲突，可并行；产出真实条目统计与明确原型说明，供 S03 验收。
- **前提**：已有聚合条目包含 favorited/applied/watched/read_later，当前鉴权提供 user；无需新接口。
- **要求**：个人计数只来自当前筛选结果并注明范围，匿名提示登录；无来源的计数移除；默认项目列表不预设筛选；
  未接入推送不能显示已开启；原型内容与操作不可伪装真实保存、发送或外部投递；空推荐理由不显示空块。
- **不做**：后端统计系统、推送配置存储、重做详情接口或重写整套页面。
- **验收**：匿名/登录/退出下计数含义明确；个人动作刷新后保留，预览动作不发业务写请求；原型标识始终可见。
- **验证**：frontend 目录 `npm run build` / `npm run lint`；隔离 real API + Playwright CLI 操作并检查请求与截图。

### S02：旧版窄屏布局（S）

- **证据 / 范围**：`frontend/src/index.css` 的 720px `.nav` 中 `flex: none` 覆盖 flex-basis；
  实测 390px 的导航右边界约 475px、下拉框约 469px；影响旧版导航、子导航与工具栏。
- **依赖**：无；与 S01 源码并行安全，最终联合验收；消费原有 DOM/class，产出局部 CSS 修复。
- **前提 / 要求**：沿用当前断点；导航与子导航在自身容器滚动，工具栏按需换行，长文可折行；不以 body 横向隐藏掩盖溢出。
- **不做**：新设计系统、组件库迁移或新工作台布局重写。
- **验收**：旧版六页在 390/768/1024/1440 无页面级横向溢出；末项导航和操作仍可达；保留桌面视觉。
- **验证**：Playwright CLI 测量 document scrollWidth/clientWidth、导航末项与下拉框可达性，查看代表性截图。

### S03：综合验收与文档（S）

- **证据 / 范围**：`IMPLEMENTATION.md`、`HANDOFF.md` 的过期状态；既有 pytest、core import 与前端 build/lint。
- **依赖**：S01/S02；文档与前端无写冲突，可先整理，结果必须等待实际验证；产出完成状态、证据、剩余边界。
- **前提 / 要求**：只读复制主库到隔离内存/临时库；验证服务端真实请求与浏览器，不将受控数据或 demo 当成外网稳定性证据。
- **不做**：写入用户主库、停止既有服务、远程 CI/发布、提交或推送。
- **验收 / 验证**：既有回归、页面实测结果如实记录；清理本轮启动的进程；仍未实现的扩展能力单列。

顺序：S01 与 S02 → S03 联合验收。风险：长标题、未登录状态、权限错误页和旧原型抽屉都需要宽度复核；远程 CI 仍未验证。

## 2026-09-22 S01/S02/S03 验收结果

### 本轮实际改动文件

`frontend/src/pages/Recruitment.tsx`、`Projects.tsx`、`AiDaily.tsx`、
`frontend/src/components/cards.tsx`、`Topbar.tsx`、`DesignPreview.tsx`、`App.tsx`、
`frontend/src/index.css`、`src/agent_kernel/adapters/api/platform.py`。

### S01 旧版数据与原型标识：完成

- 招聘/项目/AI 日报三页的固定汇总数字（7/3、21/15、模型更新 4）全部删除，改为当前筛选结果的真实计数，
  并在 note 上写明「仅当前筛选结果 · 非全账户总量」；匿名时显示「需登录」而不是 0。
- `Topbar.tsx` 通知红点固定值 6 移除；`AiDaily.tsx` 的本地日报推送假开关移除（未接入推送不显示已开启）。
- 投递文案统一为「记录投递（非外站）」/「已记录投递」，不再暗示外站提交。
- 所有详情/配置原型动作统一反馈 `操作演示：xxx；未提交、未保存、未发送。`（`DesignPreview.tsx:129`）。
- 项目页默认不再预设筛选；空推荐理由不渲染空块。

### S02 旧版窄屏布局：完成

`index.css` 修复 720px 断点下 `.nav` 的 `flex: none` 覆盖问题，导航与子导航在自身容器内滚动，
工具栏按需换行。旧版六个页面在 390 / 768 / 1024 / 1440 四档宽度下页面级 `scrollWidth - clientWidth` 均为 0，
导航末项与排序控件在 390px 下仍可达（此前旧版招聘页 390px 约 85px 溢出）。

### S03 验收与回归

2026-09-22 在项目根目录重跑（本次会话实测，非沿用旧数字）：

- `pytest tests/ -q -rs` → `158 passed, 4 skipped, 6 warnings`
  （跳过项为缺少可选依赖 litellm ×2、flashrank、daytona）
- `evals/check_core_imports.py` → exit 0
- `frontend`：`npm run build` 成功；`npm run lint` 通过，仅 3 条既有 Fast Refresh 警告
  （`src/components/ui.tsx:111`、`src/auth.tsx:78`、`src/auth.tsx:88`）

浏览器验收（同轮次隔离服务 8032/5182，独立 sqlite，未触碰 `runs/*.db`，现已关闭）：

- 双账户隔离：owner 的收藏/投递/关注/稍后阅读对 `second` 不可见；`second` 打开自动化页显示明确 403；
- 原型按钮不发送任何业务写请求，反馈为上述演示文案；
- 评分契约与页面显示一致：real 模式 `/jobs`、`/projects`、`/news` 未知评分为 JSON `null`，页面显示「未知」，
  真实 `0%` 不被误判为未知，示例评分标记「演示」，首页详情入口显式传 `score: null`。

### 仍未实现（不要当成完成）

自动化规则真实 CRUD；数据源真实配置 / OAuth / Cookie 管理；AI 日报真实生成；
邮件 / 飞书 / 微信通知；招聘网站外站自动投递；团队 / RBAC / 多租户 / 分布式 worker。
远程 CI 仍未验证（未提交、未推送）。

### 进程与工作区

本轮启动的隔离服务 `8032`/`5182` 已关闭。旧端口 `8010`/`5173`、`8021`/`5181` 可能仍有其他 agent 进程，
下轮启动前重新检查端口，不要批量 kill。工作区未提交、未推送，未跟踪文件保持原样。
