"""平台页面 REST 层：首页聚合流与五个模块页的页面级数据端点 + 写操作端点。

对齐前端的页面数据需求，每个页面一个聚合端点，一次请求返回该页渲染所需的全部
字段（列表 + 侧边栏），前端不需要拼多个接口。数据来自 `PlatformStore`（sqlite
单文件）；演示模式可以返回显式样例。真实模式的计数和日志来自内容及运行记录，
未验证的来源状态与没有记录的统计保留未知。

写端点（收藏/投递/启停/完成/重连）挂在同一 router 上：需要登录的（收藏、投递、
关注、稍后阅读）走 `Authorization: Bearer <token>` 解析用户，未登录返回 HTTP 401；
真实模式的平台管理与运行查询要求 owner。手动采集和调度共用运行记录与执行函数，
旧无认证工厂保留离线演示兼容。

响应与现有内容 API 一样是裸 JSON（无 ResponseResult 信封），字段 snake_case。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ... import module_registry
from ..auth_store import AuthManager
from ..platform_collectors import COLLECTORS, collect_for_module, execute_collection_run
from ..platform_store import PlatformStore
from ..personal_store import target_visible, visible_jobs
from .access import require_owner
from .personal import _PrivateRoute

# ---- 侧边栏演示数据（非核心业务表，先常量化） ------------------------------
JOB_FOLLOWS: list[dict[str, Any]] = [
    {"logo": "字", "logo_class": "l-byte", "name": "字节跳动", "sub": "32 个在招岗位", "pill": {"text": "+6", "variant": "green"}},
    {"logo": "T", "logo_class": "l-tencent", "name": "腾讯", "sub": "26 个在招岗位", "pill": {"text": "+3", "variant": "green"}},
    {"logo": "阿", "logo_class": "l-ali", "name": "阿里巴巴", "sub": "25 个在招岗位", "pill": {"text": "+1", "variant": ""}},
    {"logo": "mi", "logo_class": "l-mi", "name": "小米", "sub": "18 个在招岗位", "pill": {"text": "+2", "variant": ""}},
]
JOB_STATUS: list[dict[str, Any]] = [
    {"label": "已收藏", "count": 31},
    {"label": "待投递", "count": 7},
    {"label": "已投递", "count": 18},
    {"label": "面试中", "count": 3},
]
JOB_INTERVIEW: dict[str, Any] = {
    "time": "明天 14:00 · 字节跳动",
    "note": "后端一面 · 建议优先复习 JVM、MySQL 索引、Redis 一致性。",
}

PROJECT_TREND: list[dict[str, int]] = [
    {"height": 34}, {"height": 52}, {"height": 43}, {"height": 70},
    {"height": 58}, {"height": 82}, {"height": 67},
]
PROJECT_TOPICS: list[str] = ["Python", "Agent", "LLM", "RAG", "MCP", "Vector DB", "Rust", "Go"]
PROJECT_WATCHES: list[dict[str, Any]] = [
    {"name": "OpenHands", "count": "5 次更新"},
    {"name": "LangGraph", "count": "3 次更新"},
    {"name": "Milvus", "count": "1 次更新"},
]

NEWS_ENTITIES: list[dict[str, Any]] = [
    {"logo": "O", "logo_class": "l-blue", "name": "OpenAI", "sub": "今日 8 条动态", "star": True},
    {"logo": "A", "logo_class": "l-purple", "name": "Anthropic", "sub": "今日 6 条动态", "star": True},
    {"logo": "G", "logo_class": "l-orange", "name": "Google DeepMind", "sub": "今日 5 条动态", "star": True},
    {"logo": "x", "logo_class": "x", "logo_style": "#111111", "name": "xAI", "sub": "今日 2 条动态", "star": False},
]
NEWS_TOPICS: list[dict[str, Any]] = [
    {"tag": "# Agent", "count": 18},
    {"tag": "# Coding Agent", "count": 13},
    {"tag": "# MCP", "count": 9},
    {"tag": "# Model Release", "count": 8},
    {"tag": "# RAG", "count": 6},
]

AUTO_LOGS: list[dict[str, Any]] = [
    {"name": "高匹配岗位提醒", "sub": "发现 3 个新岗位", "time": "2分钟前", "error": False},
    {"name": "GitHub Agent 项目监控", "sub": "更新 5 个项目", "time": "10分钟前", "error": False},
    {"name": "BOSS 消息同步", "sub": "登录状态过期", "time": "23分钟前", "error": True},
]
AUTO_PENDING: list[dict[str, Any]] = [
    {"title": "字节跳动 · 后端实习", "desc": "Agent 已准备投递材料，等待确认。"},
    {"title": "BOSS HR 回复", "desc": "已生成回复草稿，等待发送。"},
]
AUTO_CHANNELS: list[dict[str, Any]] = [
    {"icon": "◉", "name": "站内消息", "pill": {"text": "已启用", "variant": "green"}},
    {"icon": "✉", "name": "邮件", "pill": {"text": "已启用", "variant": "green"}},
    {"icon": "◆", "name": "飞书 / 微信", "pill": {"text": "未配置", "variant": ""}},
]

SOURCE_METRICS: list[dict[str, Any]] = [
    {"label": "成功请求", "value": "12,482", "error": False},
    {"label": "新增条目", "value": "4,836", "error": False},
    {"label": "去重过滤", "value": "2,147", "error": False},
    {"label": "失败请求", "value": "36", "error": True},
]
SOURCE_ALERTS: list[dict[str, Any]] = [
    {"title": "X / Twitter", "desc": "登录状态已过期，需要重新认证。"},
    {"title": "小红书", "desc": "最近 3 次采集被限流。"},
]


class CollectionRunBody(BaseModel):
    module_id: str


def _job_actions(job: dict[str, Any]) -> list[str]:
    if job["is_referral"]:
        return ["联系内推人", "查看详情"]
    return ["收藏", "查看详情"]


# 采集来源的展示名，按行上存的 source_platform 取值索引（与 /sources 端点用的是同一批来源）
SOURCE_LABELS = {"v2ex-jobs": "V2EX 酷工作", "github": "GitHub Trending", "v2ex-hot": "V2EX AI 话题"}


def entity_meta(item: dict[str, Any]) -> str:
    """副标题：优先用存的 meta；为空时只用行上真实存在的字段拼，缺什么就不写什么。"""
    if item.get("meta"):
        return item["meta"]
    parts = [SOURCE_LABELS.get(item.get("source_platform") or ""), item.get("city") or None,
             item.get("full_name") or None, item.get("language") or None]
    return " · ".join(p for p in parts if p)


def create_platform_router(store: PlatformStore, auth_manager: AuthManager | None = None) -> APIRouter:
    router = APIRouter(route_class=_PrivateRoute)
    is_demo = auth_manager is None or auth_manager.settings.dev_mode

    # ---- 鉴权辅助 ----------------------------------------------------------
    def current_user_id(request: Request) -> int | None:
        if auth_manager is None:
            return None
        user = auth_manager.resolve_token(request.headers.get("Authorization"))
        return user["id"] if user else None

    def require_user(request: Request) -> int:
        uid = current_user_id(request)
        if uid is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return uid

    def require_target(target_type: str, target_id: int, uid: int) -> None:
        if not target_visible(store, target_type, target_id, uid):
            raise HTTPException(status_code=404, detail="target not found")

    def require_platform_owner(request: Request) -> int | None:
        return require_owner(auth_manager, request)

    def run_time(value: float | None) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else "尚未运行"

    # ---- 首页 -------------------------------------------------------------
    @router.get("/api/platform/home")
    def home(request: Request) -> dict[str, Any]:
        home_jobs = visible_jobs(store, current_user_id(request))
        if not is_demo:
            jobs = home_jobs
            projects = store.list_projects()
            news = store.list_news()
            return {
                "origin": "real",
                "stats": [
                    {"title": title, "num": str(count), "trend": "", "down": False, "sub": "当前已收录"}
                    for title, count in (("岗位", len(jobs)), ("开源项目", len(projects)),
                                         ("AI 资讯", len(news)), ("待处理事项", store.count_todos()))
                ],
                "feed": [
                    {"id": item["id"], "kind": kind, "logo": logo,
                     "title": item.get("title", item.get("name", "")),
                     "meta": entity_meta(item) or item.get("full_name") or item.get("source") or "",
                     "tags": item.get("tags", []), "time": item.get("published_at") or "发布时间未提供",
                     "desc": item.get("description", item.get("summary", "")),
                     "reason": item.get("reason", item.get("insight", "")),
                     "source_url": item.get("source_url"), "origin": item.get("origin", "unknown"),
                     "actions": [{"label": "查看详情", "primary": True}]}
                    for kind, logo, items in (("job", "mi", jobs), ("project", "git", projects),
                                              ("news", "openai", news))
                    for item in items[:2]
                ],
                "ranks": [],
                "todos": [{"id": t["id"], "name": t["name"], "sub": t["sub"],
                           "time": t["time_text"], "has_dot": bool(t["has_dot"]), "icon": t["icon"]}
                          for t in store.list_todos()],
                "automations": [{"id": a["id"], "name": a["name"], "sub": a["schedule"],
                                 "running": bool(a["enabled"])} for a in store.list_automations()
                                if a.get("module_id")],
            }
        feed: list[dict[str, Any]] = []

        job = next((j for j in home_jobs if j["source_logo"] == "mi"), None)
        if job is not None:
            feed.append({
                "kind": "job", "logo": "mi", "title": job["title"],
                "badge": {"text": "高匹配", "variant": "green"},
                "meta": job["meta"], "tags": job["tags"],
                "time": "发布于 2 小时前", "reason": job["reason"],
                "actions": [{"label": "稍后处理"}, {"label": "查看详情", "primary": True}],
            })

        project = store.list_projects()[0] if store.list_projects() else None
        if project is not None:
            feed.append({
                "kind": "project", "logo": "git", "title": project["name"],
                "badge": {"text": project["rank"], "variant": "gray"},
                "meta": f"开源项目 · {project['stars']} ⭐ · {project['language']}",
                "tags": project["tags"], "time": "更新于 4 小时前",
                "desc": project["description"], "reason": project["reason"],
                "actions": [{"label": "加入收藏"}, {"label": "查看项目", "primary": True}],
            })

        news = store.list_news()[0] if store.list_news() else None
        if news is not None:
            feed.append({
                "kind": "news", "logo": "openai", "title": news["title"],
                "badge": {"text": "AI资讯", "variant": "blue"},
                "meta": f"{news['source']} · {news['published_at']}",
                "desc": news["summary"], "reason": news["insight"],
                "actions": [{"label": "稍后阅读"}, {"label": "阅读全文", "primary": True}],
            })

        referral = next((j for j in home_jobs if j["is_referral"]), None)
        if referral is not None:
            feed.append({
                "kind": "referral", "logo": "mi", "title": referral["title"],
                "badge": {"text": "内推", "variant": "green"},
                "meta": referral["meta"], "tags": referral["tags"],
                "time": "发布于 5 小时前", "reason": referral["reason"],
                "actions": [{"label": "联系内推人"}, {"label": "查看详情", "primary": True}],
            })

        return {
            "stats": [
                {"title": "高匹配岗位", "num": str(sum(j["match"] >= 85 for j in home_jobs)), "trend": "↑ 12%", "down": False, "sub": "较昨日新增 3 个"},
                {"title": "今日推荐项目", "num": str(store.count_projects()), "trend": "↑ 33%", "down": False, "sub": "较昨日新增 2 个"},
                {"title": "AI热点", "num": str(store.count_news()), "trend": "↑ 8%", "down": False, "sub": "今日新增 4 条"},
                {"title": "待处理事项", "num": str(store.count_todos()), "trend": "↓ 2%", "down": True, "sub": "较昨日减少 1 个"},
            ],
            "feed": feed,
            "ranks": [{"title": r["title"], "heat": r["heat"]} for r in store.list_ranks()],
            "todos": [
                {"id": t["id"], "name": t["name"], "sub": t["sub"], "time": t["time_text"],
                 "has_dot": bool(t["has_dot"]), "icon": t["icon"]}
                for t in store.list_todos()
            ],
            "automations": [
                {"id": a["id"], "name": a["name"], "sub": a["schedule"], "running": bool(a["enabled"])}
                for a in store.list_automations()[:3]
            ],
        }

    # ---- 招聘 -------------------------------------------------------------
    @router.get("/api/platform/jobs")
    def jobs(
        request: Request,
        city: str | None = None,
        direction: str | None = None,
        grad_year: str | None = None,
        match_min: int | None = None,
        referral: bool = False,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {
            "city": city, "direction": direction, "grad_year": grad_year,
            "match_min": match_min, "referral": referral,
        }
        uid = current_user_id(request)
        favorites: set[int] = store.action_ids(uid, "job", "favorite") if uid is not None else set()
        applied: set[int] = store.action_ids(uid, "job", "applied") if uid is not None else set()
        return {
            "items": [
                {
                    "id": j["id"], "logo": j["logo"], "logo_class": j["logo_class"],
                    "title": j["title"], "meta": entity_meta(j), "tags": j["tags"],
                    "match": (f"{j['match']}%" if j["match"] else "未知") if is_demo else None, "salary": j["salary"], "reason": j["reason"],
                    "source_url": j.get("source_url"), "origin": j.get("origin", "unknown"),
                    "published_at": j.get("published_at"), "fetched_at": j.get("fetched_at"),
                    "referral": bool(j["is_referral"]), "actions": _job_actions(j),
                    "favorited": j["id"] in favorites, "applied": j["id"] in applied,
                }
                for j in visible_jobs(store, uid, **filters)
            ],
            "origin": "demo" if is_demo else "real",
            "follows": JOB_FOLLOWS if is_demo else [],
            "status": JOB_STATUS if is_demo else [
                {"label": "已收藏", "count": len(favorites)}, {"label": "已记录投递", "count": len(applied)},
            ],
            "interview": JOB_INTERVIEW if is_demo else {"time": "尚无面试安排", "note": ""},
        }

    # ---- 项目雷达 ----------------------------------------------------------
    @router.get("/api/platform/projects")
    def projects(
        request: Request,
        tech: str | None = None,
        language: str | None = None,
        star: str | None = Query(None, description="1k | 10k | hot"),
    ) -> dict[str, Any]:
        uid = current_user_id(request)
        favorites: set[int] = store.action_ids(uid, "project", "favorite") if uid is not None else set()
        watches: set[int] = store.action_ids(uid, "project", "watch") if uid is not None else set()
        return {
            "items": [
                {
                    "id": p["id"], "logo": p["logo"], "logo_class": p["logo_class"],
                    "name": p["name"], "full_name": p["full_name"],
                    "meta": f"{p['full_name']} · ★ {p['stars']} · {p['language']} · 今日 {p['delta']}",
                    "tags": p["tags"], "desc": p["description"], "reason": p["reason"],
                    "score": str(p["score"]) if is_demo else None, "rank": p["rank"],
                    "source_url": p.get("source_url"), "origin": p.get("origin", "unknown"),
                    "published_at": p.get("published_at"), "fetched_at": p.get("fetched_at"),
                    "actions": ["收藏", "查看项目"],
                    "favorited": p["id"] in favorites, "watched": p["id"] in watches,
                }
                for p in store.list_projects(tech=tech, language=language, star=star)
            ],
            "origin": "demo" if is_demo else "real",
            "trend": PROJECT_TREND if is_demo else [],
            "topics": PROJECT_TOPICS if is_demo else [],
            "watches": PROJECT_WATCHES if is_demo else [],
        }

    # ---- AI 日报 -----------------------------------------------------------
    @router.get("/api/platform/news")
    def news(request: Request) -> dict[str, Any]:
        uid = current_user_id(request)
        read_later: set[int] = store.action_ids(uid, "news", "read_later") if uid is not None else set()
        return {
            "items": [
                {
                    "id": n["id"], "logo": n["source_logo"], "logo_class": n["source_class"],
                    "title": n["title"], "meta": f"{n['source']} · {n['published_at']} · {n['category']}",
                    "desc": n["summary"], "score": str(n["importance"]) if is_demo else None, "insight": n["insight"],
                    "source_url": n.get("source_url"), "origin": n.get("origin", "unknown"),
                    "published_at": n.get("published_at"), "fetched_at": n.get("fetched_at"),
                    "actions": ["稍后阅读", "阅读全文"],
                    "read_later": n["id"] in read_later,
                }
                for n in store.list_news()
            ],
            "origin": "demo" if is_demo else "real",
            "entities": NEWS_ENTITIES if is_demo else [],
            "topics": NEWS_TOPICS if is_demo else [],
        }

    # ---- 自动化 ------------------------------------------------------------
    @router.get("/api/platform/automations")
    def automations(request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        runs = store.list_collection_runs() if not is_demo else []
        runtime = getattr(request.app.state, "collection_runtime", None)
        return {
            "origin": "demo" if is_demo else "real",
            "scheduler_enabled": bool(runtime and runtime.scheduler),
            "stats": {"total": len(runs), "succeeded": sum(r["status"] == "succeeded" for r in runs),
                      "failed": sum(r["status"] in {"failed", "partial", "interrupted"} for r in runs)},
            "rules": [
                {
                    "id": a["id"], "title": a["name"], "meta": a["schedule"], "category": a["category"],
                    "flow": a["flow"], "last": a["next_run"], "enabled": bool(a["enabled"]),
                    "module_id": a.get("module_id"),
                    "actions": ["编辑", "立即执行"],
                }
                for a in store.list_automations()
                if is_demo or a.get("module_id")
            ],
            "logs": AUTO_LOGS if is_demo else [
                {"id": r["id"], "name": r["module_id"], "status": r["status"],
                 "sub": r["error"] or f"{r['status']} · 新增 {r['inserted']} / 更新 {r['updated']}",
                 "time": run_time(r["finished_at"] or r["started_at"]),
                 "error": r["status"] in {"failed", "partial", "interrupted"}} for r in runs
            ],
            "pending": AUTO_PENDING if is_demo else [],
            "channels": AUTO_CHANNELS if is_demo else [],
        }

    # ---- 数据源 ------------------------------------------------------------
    @router.get("/api/platform/sources")
    def sources(request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        if not is_demo:
            items = []
            for source_id, module, name, category in (
                (1, "job-hunter", "V2EX 酷工作", "招聘平台"),
                (2, "github-trending", "GitHub Trending", "项目平台"),
                (3, "ai-daily-news", "V2EX AI 话题", "新闻平台"),
            ):
                runs = store.list_collection_runs(module, 1)
                latest = runs[0] if runs else None
                status = latest["status"] if latest else "unknown"
                items.append({"id": source_id, "module_id": module, "logo": "源", "logo_class": "l-blue",
                              "logo_style": None, "name": name, "sub": "公开来源 · 按需采集",
                              "category": category, "status": status,
                              "status_text": {"unknown": "尚未验证", "succeeded": "最近成功", "failed": "最近失败",
                                              "queued": "等待采集", "running": "采集中", "partial": "部分成功",
                                              "interrupted": "运行中断"}[status],
                              "last": run_time(latest["finished_at"] if latest else None),
                              "today": None, "action": "查看运行记录 ›"})
            return {"origin": "real", "items": items, "metrics": [],
                    "alerts": [{"title": item["name"], "desc": item["status_text"]} for item in items
                               if item["status"] in {"failed", "partial", "interrupted"}]}
        return {
            "items": [
                {
                    "id": s["id"], "logo": s["logo"], "logo_class": s["logo_class"],
                    "logo_style": s["logo_style"] or None, "name": s["name"],
                    "sub": s["description"], "category": s["category"],
                    "status": s["status"], "status_text": s["status_text"],
                    "last": s["last_run"], "today": s["today_items"],
                    "action": "重新认证 ›" if s["status"] == "warn" else "管理 ›",
                }
                for s in store.list_sources()
            ],
            "metrics": SOURCE_METRICS,
            "alerts": SOURCE_ALERTS,
        }

    # ---- 写操作 ------------------------------------------------------------
    @router.post("/api/platform/jobs/{job_id}/favorite")
    def favorite_job(job_id: int, request: Request) -> dict[str, Any]:
        uid = require_user(request)
        require_target("job", job_id, uid)
        favorited = store.toggle_action(uid, "job", job_id, "favorite")
        return {"favorited": favorited}

    @router.post("/api/platform/jobs/{job_id}/apply")
    def apply_job(job_id: int, request: Request) -> dict[str, Any]:
        uid = require_user(request)
        require_target("job", job_id, uid)
        applied = store.add_action(uid, "job", job_id, "applied")
        return {"applied": applied}

    @router.post("/api/platform/projects/{project_id}/favorite")
    def favorite_project(project_id: int, request: Request) -> dict[str, Any]:
        uid = require_user(request)
        require_target("project", project_id, uid)
        favorited = store.toggle_action(uid, "project", project_id, "favorite")
        return {"favorited": favorited}

    @router.post("/api/platform/projects/{project_id}/watch")
    def watch_project(project_id: int, request: Request) -> dict[str, Any]:
        uid = require_user(request)
        require_target("project", project_id, uid)
        watched = store.toggle_action(uid, "project", project_id, "watch")
        return {"watched": watched}

    @router.post("/api/platform/news/{news_id}/read_later")
    def read_later(news_id: int, request: Request) -> dict[str, Any]:
        uid = require_user(request)
        require_target("news", news_id, uid)
        saved = store.toggle_action(uid, "news", news_id, "read_later")
        return {"read_later": saved}

    @router.post("/api/platform/automations/{automation_id}/toggle")
    def toggle_automation(automation_id: int, request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        rule = next((a for a in store.list_automations() if a["id"] == automation_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="automation not found")
        if not is_demo and rule.get("module_id") not in COLLECTORS:
            raise HTTPException(status_code=409, detail="规则未关联可执行采集模块")
        runtime = getattr(request.app.state, "collection_runtime", None)
        if runtime and rule.get("module_id"):
            return {"enabled": runtime.toggle_rule(automation_id)}
        store.toggle_automation(automation_id, not bool(rule["enabled"]))
        return {"enabled": not bool(rule["enabled"])}

    @router.post("/api/platform/todos/{todo_id}/done")
    def done_todo(todo_id: int, request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        if not any(t["id"] == todo_id for t in store.list_todos()):
            raise HTTPException(status_code=404, detail="todo not found")
        return {"done": store.done_todo(todo_id)}

    @router.post("/api/platform/sources/{source_id}/reconnect")
    def reconnect_source(source_id: int, request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        if not is_demo:
            raise HTTPException(status_code=409, detail="此来源没有认证连接；请主动采集验证可用性")
        return {"reconnected": store.reconnect_source(source_id)}

    @router.post("/api/platform/collect/{module_id}")
    def collect(module_id: str, request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        if module_id not in COLLECTORS:
            raise HTTPException(status_code=404, detail="module not found")
        if is_demo:
            return {"module": module_id, "collected": collect_for_module(store, module_id)}
        connection = store.fork()
        try:
            run_id, created = connection.create_collection_run(module_id, "manual")
            if not created:
                raise HTTPException(status_code=409, detail={"message": "采集正在进行", "run_id": run_id})
            execute_collection_run(store, module_id, run_id)
            run = connection.get_collection_run(run_id)
            if run["status"] != "succeeded":
                raise HTTPException(status_code=502, detail={"message": run["error"], "run_id": run_id})
            return {"module": module_id, "collected": run["inserted"]}
        finally:
            connection.close()

    @router.post("/api/platform/collection-runs", status_code=202)
    def start_collection_run(
        body: CollectionRunBody,
        request: Request,
    ) -> dict[str, Any]:
        require_platform_owner(request)
        try:
            module_registry.get(body.module_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="module not found") from exc
        runtime = getattr(request.app.state, "collection_runtime", None)
        if runtime is None:
            raise HTTPException(status_code=503, detail="采集运行服务尚未启动")
        return runtime.trigger(body.module_id)

    @router.get("/api/platform/collection-runs")
    def list_collection_runs(request: Request, module_id: str | None = None,
                             limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        require_platform_owner(request)
        return {"items": store.list_collection_runs(module_id, limit)}

    @router.get("/api/platform/collection-runs/{run_id}")
    def get_collection_run(run_id: str, request: Request) -> dict[str, Any]:
        require_platform_owner(request)
        run = store.get_collection_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="collection run not found")
        return run

    return router
