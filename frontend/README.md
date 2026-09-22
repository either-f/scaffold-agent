# frontend

内容看板：读三个模块（招聘 / 项目挖掘 / AI日报）的采集结果，job-hunter 模块额外提供审批（通过/拒绝）操作。
不认模块名，导航栏读 `GET /api/modules` 动态生成——后端新增模块不用改前端代码。

## 依赖后端

需要 `src/agent_kernel/adapters/api/app.py` 起的 FastAPI 服务先跑起来（默认约定 `http://127.0.0.1:8000`，
CORS 已放开）。启动方式见仓库根 README.md 的「API 层」小节。

## 本地开发

```powershell
cd frontend
npm install
copy .env.example .env   # 按需改 VITE_API_BASE
npm run dev
```

## 构建

```powershell
npm run build   # tsc -b && vite build，产物在 dist/
```

## 结构

- `src/api.ts` — 唯一的 fetch 封装层，`VITE_API_BASE` 是唯一的后端地址来源。
- `src/types.ts` — `ModuleInfo` / `ContentItem`，跟后端 `ContentItem` dataclass 字段一一对应。
- `src/components/ModuleNav.tsx` — 左侧模块导航，数据来自 `/api/modules`。
- `src/components/ContentList.tsx` — 列表 + 状态筛选。
- `src/components/ItemDetail.tsx` — 详情面板：`structured` 字段通用 key/value 展示（不为每个模块写死字段名），
  只有当前模块 `has_approval === true` 时才渲染「通过/拒绝」按钮。

跳过的东西：没上 react-router（一个 useState 存 moduleId 够用，三个模块间没有需要独立 URL 的场景），
没上状态管理库（页面就这一层，useState 够用），没做登录鉴权（YAGNI，等真的要多用户再加）。
