"""平台页面数据存储：招聘/项目/AI日报/自动化/数据源 + 首页聚合流。

对应前端 6 个页面（首页 + 招聘 + 项目雷达 + AI日报 + 自动化 + 数据源）的数据源。
跟 content_store.py 同一套 sqlite 单文件模式，零外部服务依赖；`seed_demo()` 灌入
与前端设计稿一致的演示数据（幂等：表非空即跳过），首次启动由调用方决定是否执行。

字段命名与 JSON 序列化统一 snake_case，与 `adapters/api/platform.py` 返回给前端的
结构一一对应；侧边栏的少量展示性数据（关注列表、求职状态、采集概览等）不建表，
由路由层作为常量返回——它们不是核心业务表，真接外部平台时换成各自 adapter 即可。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag


def _now() -> float:
    return time.time()


class PlatformStore:
    def __init__(
        self,
        path: str = ":memory:",
        *,
        _connect_path: str | None = None,
        _connect_uri: bool = False,
        _family_lock: threading.RLock | None = None,
        _initialize: bool = True,
    ) -> None:
        self.path = path
        if _connect_path is None and path == ":memory:":
            _connect_path = f"file:platform_store_{uuid.uuid4().hex}?mode=memory&cache=shared"
            _connect_uri = True
        self._connect_path = _connect_path or str(Path(path).resolve())
        self._connect_uri = _connect_uri
        self.conn = sqlite3.connect(
            self._connect_path,
            check_same_thread=False,
            timeout=30,
            uri=self._connect_uri,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._run_lock = threading.RLock()
        self._write_lock = self._run_lock
        # Shared-cache SQLITE_LOCKED ignores busy_timeout; serialize this memory family.
        self._family_lock = _family_lock or (threading.RLock() if path == ":memory:" else None)
        self._db_lock = self._family_lock or self._write_lock
        self._closed = False
        if _initialize:
            self._create_tables()

    @contextmanager
    def _write_transaction(self):
        """同一连接内的短写事务；网络请求必须在调用方进入前完成。"""
        with self._db_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def fork(self) -> "PlatformStore":
        """为后台 worker 打开同一数据库的独立连接。"""
        return type(self)(
            self.path,
            _connect_path=self._connect_path,
            _connect_uri=self._connect_uri,
            _family_lock=self._family_lock,
            _initialize=False,
        )

    def close(self) -> None:
        with self._db_lock:
            if self._closed:
                return
            self.conn.close()
            self._closed = True

    # ---- 建表 -------------------------------------------------------------
    def _create_tables(self) -> None:
        with self._write_transaction():
            self._create_tables_unlocked()

    def _create_tables_unlocked(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "company TEXT NOT NULL, logo TEXT NOT NULL, logo_class TEXT NOT NULL, "
            "title TEXT NOT NULL, city TEXT NOT NULL, direction TEXT NOT NULL, "
            "grad_year TEXT NOT NULL, meta TEXT NOT NULL, salary TEXT NOT NULL, "
            "match INTEGER NOT NULL, tags TEXT NOT NULL DEFAULT '[]', "
            "reason TEXT NOT NULL DEFAULT '', is_referral INTEGER NOT NULL DEFAULT 0, "
            "source_logo TEXT NOT NULL DEFAULT '', created_at REAL, "
            "source_platform TEXT, external_id TEXT, source_url TEXT, "
            "origin TEXT NOT NULL DEFAULT 'unknown', published_at TEXT, "
            "fetched_at REAL, updated_at REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS projects("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, full_name TEXT NOT NULL, "
            "logo TEXT NOT NULL, logo_class TEXT NOT NULL, "
            "stars TEXT NOT NULL, stars_int INTEGER NOT NULL DEFAULT 0, "
            "language TEXT NOT NULL, delta TEXT NOT NULL, delta_int INTEGER NOT NULL DEFAULT 0, "
            "tech TEXT NOT NULL, score INTEGER NOT NULL, rank TEXT NOT NULL, "
            "tags TEXT NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '', "
            "reason TEXT NOT NULL DEFAULT '', created_at REAL, "
            "source_platform TEXT, external_id TEXT, source_url TEXT, "
            "origin TEXT NOT NULL DEFAULT 'unknown', published_at TEXT, "
            "fetched_at REAL, updated_at REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS news("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, source TEXT NOT NULL, "
            "source_logo TEXT NOT NULL, source_class TEXT NOT NULL, "
            "category TEXT NOT NULL, published_at TEXT, "
            "importance INTEGER NOT NULL, heat TEXT NOT NULL DEFAULT '', "
            "summary TEXT NOT NULL DEFAULT '', insight TEXT NOT NULL DEFAULT '', "
            "created_at REAL, source_platform TEXT, external_id TEXT, source_url TEXT, "
            "origin TEXT NOT NULL DEFAULT 'unknown', fetched_at REAL, updated_at REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS automations("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, category TEXT NOT NULL, schedule TEXT NOT NULL, "
            "flow TEXT NOT NULL DEFAULT '[]', next_run TEXT NOT NULL DEFAULT '', "
            "last_result TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, "
            "created_at REAL, module_id TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS sources("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, category TEXT NOT NULL, "
            "logo TEXT NOT NULL, logo_class TEXT NOT NULL, logo_style TEXT NOT NULL DEFAULT '', "
            "description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok', "
            "status_text TEXT NOT NULL DEFAULT '运行中', last_run TEXT NOT NULL DEFAULT '', "
            "today_items INTEGER NOT NULL DEFAULT 0, created_at REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS todos("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, sub TEXT NOT NULL DEFAULT '', time_text TEXT NOT NULL DEFAULT '', "
            "has_dot INTEGER NOT NULL DEFAULT 0, icon TEXT NOT NULL DEFAULT 'task', "
            "created_at REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS ranks("
            "position INTEGER PRIMARY KEY, title TEXT NOT NULL, heat TEXT NOT NULL)"
        )
        source_columns = (
            ("source_platform", "TEXT"),
            ("external_id", "TEXT"),
            ("source_url", "TEXT"),
            ("origin", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("published_at", "TEXT"),
            ("fetched_at", "REAL"),
            ("updated_at", "REAL"),
        )
        for table in ("jobs", "projects", "news"):
            existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in source_columns:
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(automations)")}
        if "module_id" not in existing:
            self.conn.execute("ALTER TABLE automations ADD COLUMN module_id TEXT")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS user_actions("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, target_type TEXT NOT NULL, target_id INTEGER NOT NULL, "
            "action TEXT NOT NULL, created_at REAL, "
            "UNIQUE(user_id, target_type, target_id, action))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS collection_runs("
            "id TEXT PRIMARY KEY, module_id TEXT NOT NULL, trigger TEXT NOT NULL, "
            "status TEXT NOT NULL, started_at REAL, finished_at REAL, "
            "inserted INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0, "
            "skipped INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', "
            "created_at REAL NOT NULL)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_runs_module_status "
            "ON collection_runs(module_id, status)"
        )
        self._repair_active_run_duplicates()
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_runs_active_module "
            "ON collection_runs(module_id) WHERE status IN ('queued','running')"
        )
        for table in ("jobs", "projects", "news"):
            self.conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_source_identity "
                f"ON {table}(source_platform, external_id) "
                "WHERE source_platform IS NOT NULL AND source_platform <> '' "
                "AND external_id IS NOT NULL AND external_id <> ''"
            )
            self.conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_source_url "
                f"ON {table}(source_platform, source_url) "
                "WHERE source_platform IS NOT NULL AND source_platform <> '' "
                "AND source_url IS NOT NULL AND source_url <> ''"
            )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_automations_module "
            "ON automations(module_id) WHERE module_id IS NOT NULL AND module_id <> ''"
        )

    def _repair_active_run_duplicates(self) -> None:
        modules = self.conn.execute(
            "SELECT module_id FROM collection_runs "
            "WHERE status IN ('queued','running') GROUP BY module_id HAVING COUNT(*) > 1"
        ).fetchall()
        for module in modules:
            rows = self.conn.execute(
                "SELECT id FROM collection_runs WHERE module_id=? AND status IN ('queued','running') "
                "ORDER BY created_at ASC, id ASC",
                (module["module_id"],),
            ).fetchall()
            for row in rows[1:]:
                self.conn.execute(
                    "UPDATE collection_runs SET status='interrupted', finished_at=?, error=? WHERE id=?",
                    (_now(), "interrupted", row["id"]),
                )

    # ---- 读 ---------------------------------------------------------------
    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._db_lock:
            out: list[dict[str, Any]] = []
            for row in self.conn.execute(sql, params).fetchall():
                item = dict(row)
                for key in ("tags", "flow"):
                    if key in item and isinstance(item[key], str):
                        item[key] = json.loads(item[key])
                out.append(item)
            return out

    def list_jobs(self, **filters: str | int | None) -> list[dict[str, Any]]:
        where, params = [], []
        for key, value in filters.items():
            if value in (None, ""):
                continue
            if key == "referral":
                # 仅当显式传 True 时才加内推过滤；False/缺省都不过滤
                if not value:
                    continue
                where.append("is_referral = 1")
                continue
            if key == "match_min":
                where.append("match >= ?")
                params.append(int(value))
            elif key in ("city", "direction", "grad_year", "tech", "language") and "," in str(value):
                vals = [v for v in str(value).split(",") if v]
                where.append(f"{key} IN ({','.join('?' * len(vals))})")
                params.extend(vals)
            else:
                where.append(f"{key} = ?")
                params.append(str(value))
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY match DESC, id ASC"
        return self._rows(sql, tuple(params))

    def list_projects(self, **filters: str | int | None) -> list[dict[str, Any]]:
        where, params = [], []
        for key, value in filters.items():
            if value in (None, ""):
                continue
            if key == "star":
                if value == "10k":
                    where.append("stars_int >= 10000")
                elif value == "hot":
                    where.append("delta_int > 0")
                else:
                    where.append("stars_int >= 1000")
            elif key in ("tech", "language") and "," in str(value):
                vals = [v for v in str(value).split(",") if v]
                where.append(f"{key} IN ({','.join('?' * len(vals))})")
                params.extend(vals)
            else:
                where.append(f"{key} = ?")
                params.append(str(value))
        sql = "SELECT * FROM projects"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY score DESC, id ASC"
        return self._rows(sql, tuple(params))

    def list_news(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM news ORDER BY importance DESC, id ASC")

    def list_automations(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM automations ORDER BY id ASC")

    def ensure_collection_automations(self, modules: list[dict]) -> None:
        """为真实模块补齐采集规则；旧的 demo 规则保持 module_id=NULL。"""
        with self._write_transaction():
            for module in modules:
                module_id = str(module["id"])
                label = str(module["label"])
                schedule = str(module["schedule"])
                values = (label, label, schedule, json.dumps(["采集", "保存"], ensure_ascii=False))
                self.conn.execute(
                    "INSERT INTO automations(name, category, schedule, flow, next_run, last_result, "
                    "enabled, created_at, module_id) VALUES(?,?,?,?,?,?,1,?,?) "
                    "ON CONFLICT(module_id) WHERE module_id IS NOT NULL AND module_id <> '' DO NOTHING",
                    (*values, "", "", _now(), module_id),
                )

    def list_sources(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM sources ORDER BY id ASC")

    def list_todos(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM todos ORDER BY id ASC")

    def list_ranks(self) -> list[dict[str, Any]]:
        return self._rows("SELECT title, heat FROM ranks ORDER BY position ASC")

    def count_jobs(self, match_min: int = 0, referral: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM jobs WHERE match >= ?"
        params: tuple = (match_min,)
        if referral:
            sql += " AND is_referral = 1"
        with self._db_lock:
            return int(self.conn.execute(sql, params).fetchone()["n"])

    def count_projects(self) -> int:
        with self._db_lock:
            return int(self.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"])

    def count_news(self) -> int:
        with self._db_lock:
            return int(self.conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()["n"])

    def count_todos(self) -> int:
        with self._db_lock:
            return int(self.conn.execute("SELECT COUNT(*) AS n FROM todos").fetchone()["n"])

    def target_exists(self, target_type: str, target_id: int) -> bool:
        """检查用户动作的目标，避免给不存在的对象写孤儿记录。"""
        tables = {"job": "jobs", "project": "projects", "news": "news"}
        table = tables.get(target_type)
        if table is None:
            return False
        with self._db_lock:
            row = self.conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (target_id,)).fetchone()
            return row is not None

    # ---- 采集运行记录 -----------------------------------------------------
    def create_collection_run(self, module_id: str, trigger: str) -> tuple[str, bool]:
        """原子创建 queued；冲突时读回同模块现存活动 run。"""
        with self._write_transaction():
            run_id = uuid.uuid4().hex
            cur = self.conn.execute(
                "INSERT INTO collection_runs(id, module_id, trigger, status, created_at) "
                "VALUES(?,?,?,'queued',?) "
                "ON CONFLICT(module_id) WHERE status IN ('queued','running') DO NOTHING",
                (run_id, module_id, trigger, _now()),
            )
            if cur.rowcount > 0:
                return run_id, True
            active = self.conn.execute(
                "SELECT id FROM collection_runs WHERE module_id=? AND status IN ('queued','running') "
                "ORDER BY created_at ASC, id ASC LIMIT 1",
                (module_id,),
            ).fetchone()
            if active is None:
                raise RuntimeError("collection run conflict could not be resolved")
            return str(active["id"]), False

    def interrupt_collection_runs(self) -> int:
        """生命周期启动时终止上次进程遗留的 queued/running 运行。"""
        with self._write_transaction():
            cur = self.conn.execute(
                "UPDATE collection_runs SET status='interrupted', finished_at=?, error=? "
                "WHERE status IN ('queued','running')",
                (_now(), "interrupted"),
            )
            return cur.rowcount

    def update_collection_run(self, run_id: str, *, status: str | None = None,
                              inserted: int | None = None, updated: int | None = None,
                              skipped: int | None = None, error: str | None = None) -> bool:
        """更新运行记录；status='running' 是一次性的 queued->running claim。"""
        fields: list[str] = []
        params: list[Any] = []
        where = "id=? AND status IN ('queued','running')"
        if status is not None:
            if status not in {"queued", "running", "succeeded", "partial", "failed", "interrupted"}:
                raise ValueError("invalid collection status")
            fields.append("status=?")
            params.append(status)
            if status == "running":
                fields.append("started_at=?")
                params.append(_now())
                where += " AND status='queued'"
            elif status in {"succeeded", "partial", "failed", "interrupted"}:
                fields.append("finished_at=?")
                params.append(_now())
            elif status == "queued":
                where += " AND status='queued'"
        for name, value in (("inserted", inserted), ("updated", updated), ("skipped", skipped), ("error", error)):
            if value is not None:
                fields.append(f"{name}=?")
                params.append(value)
        if not fields:
            return False
        with self._write_transaction():
            params.append(run_id)
            cur = self.conn.execute(
                f"UPDATE collection_runs SET {', '.join(fields)} WHERE {where}", tuple(params)
            )
            return cur.rowcount > 0

    def get_collection_run(self, run_id: str) -> dict[str, Any] | None:
        with self._db_lock:
            row = self.conn.execute("SELECT * FROM collection_runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row is not None else None

    def list_collection_runs(self, module_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if module_id:
            return self._rows(
                "SELECT * FROM collection_runs WHERE module_id=? ORDER BY created_at DESC LIMIT ?",
                (module_id, limit),
            )
        return self._rows("SELECT * FROM collection_runs ORDER BY created_at DESC LIMIT ?", (limit,))

    # ---- seed（幂等） ------------------------------------------------------
    def seed_demo(self) -> None:
        with self._write_transaction():
            self._seed_demo_unlocked()

    def _seed_demo_unlocked(self) -> None:
        """灌入与前端设计稿一致的演示数据；已有数据则跳过。"""
        if self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] > 0:
            return
        now = _now()

        jobs = [
            ("字节跳动", "字", "l-byte", "后端开发工程师（推荐系统方向）", "深圳", "后端开发", "2027届",
             "字节跳动 · 深圳 · 实习 · 本科及以上 · 2027届", "25-40K · 16薪", 92,
             ["Java", "推荐系统", "MySQL", "Kafka"],
             "你的后端项目经历与岗位技术栈重合度高，同时具备 Agent / RAG 工程经验，可作为差异化亮点。", 0, ""),
            ("腾讯", "T", "l-tencent", "后台开发工程师（基础架构）", "深圳", "后端开发", "2027届",
             "腾讯 · 深圳 · 校招 · 本科及以上 · 2027届", "24-38K · 16薪", 89,
             ["C++", "Java", "Linux", "分布式"],
             "系统设计与分布式部分与你当前学习方向一致，但 C++ 深度是主要缺口。", 0, ""),
            ("阿里巴巴", "阿", "l-ali", "Java 研发工程师", "杭州", "后端开发", "2027届",
             "阿里巴巴 · 杭州 · 实习 · 本科及以上 · 2027届", "22-35K", 86,
             ["Java", "Spring", "Redis", "中间件"],
             "技术栈匹配，但建议投递前强化 Spring / JVM / 高并发相关项目表达。", 0, ""),
            ("小米", "mi", "l-mi", "大模型算法工程师（NLP方向）", "北京", "算法", "不限",
             "小米 · 北京 · 30-50K · 3-5年", "30-50K", 95,
             ["NLP", "大模型", "PyTorch", "分布式训练"],
             "你的大模型项目经验与该岗位要求高度匹配；在 NLP、PyTorch、分布式训练方面具备优势。", 0, "mi"),
            ("腾讯", "鹅", "l-tencent", "后台开发工程师（内推）", "深圳", "后端开发", "2027届",
             "腾讯 · 深圳 · 校招 · 内推优先", "25-40K · 16薪", 88,
             ["Go", "微服务", "分布式"],
             "内推渠道：朋友在基础架构组，可直达主管，反馈更快。", 1, ""),
            ("字节跳动", "抖", "l-blue", "推荐算法工程师（内推）", "北京", "算法", "不限",
             "字节跳动 · 北京 · 社招 · 内推", "30-50K", 84,
             ["推荐", "ML", "Python"],
             "内推人可帮忙看简历、内推系统直达，避免海投石沉大海。", 1, ""),
            ("美团", "团", "l-orange", "Java 后端工程师（内推）", "北京", "后端开发", "2027届",
             "美团 · 北京 · 校招 · 内推", "22-38K", 82,
             ["Java", "Spring", "Kafka"],
             "内推渠道反馈快，面试流程可加速。", 1, ""),
        ]
        for j in jobs:
            self.conn.execute(
                "INSERT INTO jobs(company, logo, logo_class, title, city, direction, grad_year, "
                "meta, salary, match, tags, reason, is_referral, source_logo, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*j[:9], j[9], json.dumps(j[10]), j[11], j[12], j[13], now),
            )

        projects = [
            ("OpenHands", "All-Hands-AI/OpenHands", "GH", "l-gh", "58.2k", 58200, "Python", "+2.3k", 2300,
             "Agent", 92, "Trending #1", ["Agent", "Coding", "Sandbox", "LLM"],
             "一个开放式的软件开发 Agent 平台，支持任务规划、代码编辑、终端执行与浏览器工具。",
             "与你当前 Agent Runtime、Sandbox、Tool Execution 方向高度相关，适合重点拆解。"),
            ("LangGraph", "langchain-ai/langgraph", "LG", "l-blue", "46.1k", 46100, "Python", "+1.8k", 1800,
             "Agent", 90, "Trending #3", ["Graph", "Agent", "Workflow", "Checkpoint"],
             "面向有状态 Agent 工作流的图式编排框架，支持持久化、恢复和复杂流程控制。",
             "适合与你自己的 Planner / Checkpoint / Event 设计进行对照研究。"),
            ("Milvus", "milvus-io/milvus", "M", "l-purple", "23.4k", 23400, "Go", "+650", 650,
             "Vector DB", 85, "今日 +12 位", ["Vector DB", "RAG", "Database"],
             "面向大规模向量检索的开源数据库，适用于 RAG、推荐系统与语义搜索。",
             "与你现有 Memory / Hybrid RAG 模块直接相关，适合补生产化检索经验。"),
        ]
        for p in projects:
            self.conn.execute(
                "INSERT INTO projects(name, full_name, logo, logo_class, stars, stars_int, language, "
                "delta, delta_int, tech, score, rank, tags, description, reason, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*p[:12], json.dumps(p[12]), p[13], p[14], now),
            )

        news = [
            ("OpenAI 发布新的 API 能力与价格调整", "OpenAI 官方", "O", "l-blue", "模型", "1 小时前", 95, "12.3k",
             "新的推理与工具调用能力开放，同时部分模型价格下调，面向 Agent 场景的开发成本进一步降低。",
             "这项变化会直接影响 Agent 服务的模型路由和成本控制策略，建议优先关注调用价格与工具调用行为变化。"),
            ("Anthropic 更新 Claude Code 与工具使用能力", "Anthropic", "A", "l-purple", "Coding Agent", "2 小时前", 92, "9.8k",
             "Claude Code 增强长任务执行、工具调用与上下文管理，开发流程自动化能力继续增强。",
             "值得重点观察其 Agent 任务恢复、工具执行和多轮上下文策略，与你当前项目方向高度相关。"),
            ("Google Gemini 推出新版本，强化长上下文与实时能力", "Google DeepMind", "G", "l-orange", "Gemini", "4 小时前", 88, "7.6k",
             "新版本重点优化长上下文、响应延迟和多模态理解能力，适合更复杂的实时 AI 产品。",
             "如果未来平台要做长文本信息处理或多模态采集，这类模型值得进入候选池。"),
        ]
        for n in news:
            self.conn.execute(
                "INSERT INTO news(title, source, source_logo, source_class, category, published_at, "
                "importance, heat, summary, insight, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*n, now),
            )

        automations = [
            ("每日岗位订阅", "招聘", "每天 08:00 执行 · 最近成功 2 分钟前",
             ["定时触发", "采集岗位", "偏好过滤", "AI 匹配", "首页推荐"], "下一次：明天 08:00", ""),
            ("高匹配岗位提醒", "招聘", "实时触发 · 匹配度 ≥ 85%",
             ["新岗位", "匹配度 ≥ 85%", "AI 推荐理由", "通知用户"], "今日已触发 6 次", ""),
            ("GitHub Agent 项目监控", "项目雷达", "每小时执行 · Star 增长 / Release / Commit",
             ["GitHub", "变化检测", "AI 分析", "推荐度排序", "项目雷达"], "下一次：11:00", ""),
            ("AI 日报自动推送", "AI日报", "每天 09:00 · 重要度 ≥ 70",
             ["多源采集", "去重聚类", "摘要", "个性化排序", "推送"], "下一次：明天 09:00", ""),
        ]
        for a in automations:
            self.conn.execute(
                "INSERT INTO automations(name, category, schedule, flow, next_run, last_result, enabled, created_at) "
                "VALUES(?,?,?,?,?,?,1,?)",
                (*a[:3], json.dumps(a[3]), a[4], a[5], now),
            )

        sources = [
            ("BOSS 直聘", "招聘平台", "B", "l-byte", "", "招聘岗位 / 消息", "ok", "运行中", "2分钟前", 128),
            ("智联招聘", "招聘平台", "智", "l-blue", "", "招聘岗位", "ok", "运行中", "3分钟前", 86),
            ("字节跳动校园招聘", "官方网站", "字", "l-purple", "", "企业官网", "ok", "运行中", "5分钟前", 42),
            ("GitHub Trending", "项目平台", "GH", "l-gh", "", "项目榜单 / 仓库", "ok", "运行中", "10分钟前", 46),
            ("AIHot", "新闻平台", "AI", "l-orange", "", "AI 资讯聚合", "ok", "运行中", "15分钟前", 57),
            ("X / Twitter", "社交平台", "X", "x", "#111111", "关键人物动态", "warn", "需认证", "2小时前", 0),
        ]
        for s in sources:
            self.conn.execute(
                "INSERT INTO sources(name, category, logo, logo_class, logo_style, description, "
                "status, status_text, last_run, today_items, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*s, now),
            )

        todos = [
            ("Boss 直聘 · 新消息", "字节跳动 · 2轮技术面试邀请", "10 分钟前", 1, "task"),
            ("GitHub · 3 个项目更新", "3 个项目有新动态", "1 小时前", 1, "git"),
            ("内推 · 2 个新机会", "来自校友的内推机会", "2 小时前", 0, "link"),
        ]
        for t in todos:
            self.conn.execute(
                "INSERT INTO todos(name, sub, time_text, has_dot, icon, created_at) VALUES(?,?,?,?,?,?)",
                (*t, now),
            )

        ranks = [
            (1, "AGI 何时到来？深度长文复盘", "12.3k"),
            (2, "字节跳动 Seed 团队发布图像模型...", "9.8k"),
            (3, "Claude 3.5 系列模型全面开放", "7.6k"),
            (4, "国内大模型产品全景对比（6月版）", "6.1k"),
            (5, "Midjourney V6 正式版发布", "5.4k"),
        ]
        self.conn.executemany(
            "INSERT INTO ranks(position, title, heat) VALUES(?,?,?)", ranks,
        )

    # ---- 写（用户动作） -----------------------------------------------------
    def add_action(self, user_id: int, target_type: str, target_id: int, action: str) -> bool:
        """新增用户动作（收藏/投递/关注等），幂等：已存在返回 False。"""
        with self._write_transaction():
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO user_actions(user_id, target_type, target_id, action, created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, target_type, target_id, action, _now()),
            )
            return cur.rowcount > 0

    def remove_action(self, user_id: int, target_type: str, target_id: int, action: str) -> None:
        with self._write_transaction():
            self.conn.execute(
                "DELETE FROM user_actions WHERE user_id=? AND target_type=? AND target_id=? AND action=?",
                (user_id, target_type, target_id, action),
            )

    def toggle_action(self, user_id: int, target_type: str, target_id: int, action: str) -> bool:
        """切换型动作（收藏/关注等）：不存在则新增返回 True，已存在则删除返回 False。"""
        with self._write_transaction():
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO user_actions(user_id, target_type, target_id, action, created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, target_type, target_id, action, _now()),
            )
            if cur.rowcount > 0:
                return True
            self.conn.execute(
                "DELETE FROM user_actions WHERE user_id=? AND target_type=? AND target_id=? AND action=?",
                (user_id, target_type, target_id, action),
            )
            return False

    def action_ids(self, user_id: int, target_type: str, action: str) -> set[int]:
        with self._db_lock:
            rows = self.conn.execute(
                "SELECT target_id FROM user_actions WHERE user_id=? AND target_type=? AND action=?",
                (user_id, target_type, action),
            ).fetchall()
            return {int(r["target_id"]) for r in rows}

    def toggle_automation(self, automation_id: int, enabled: bool) -> bool:
        with self._write_transaction():
            cur = self.conn.execute(
                "UPDATE automations SET enabled=? WHERE id=?", (1 if enabled else 0, automation_id),
            )
            return cur.rowcount > 0

    def done_todo(self, todo_id: int) -> bool:
        with self._write_transaction():
            cur = self.conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
            return cur.rowcount > 0

    def reconnect_source(self, source_id: int) -> bool:
        with self._write_transaction():
            cur = self.conn.execute(
                "UPDATE sources SET status='ok', status_text='运行中', last_run='刚刚' WHERE id=? AND status='warn'",
                (source_id,),
            )
            return cur.rowcount > 0

    # ---- 采集 upsert（真实采集写入） ----------------------------------------
    @staticmethod
    def _clean_source_value(value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _upsert_source_result(
        self, table: str, values: dict[str, Any], defaults: dict[str, Any], *,
        source_platform: str | None, external_id: str | None, source_url: str | None,
        origin: str, published_at: str | None, fetched_at: float | None,
    ) -> str:
        """Native UPSERT plus a locked snapshot for accurate insert/update/skip counts.

        Missing identities never match legacy rows by title. fetched_at tracks each
        observation; updated_at changes only when supplied content/metadata changes.
        """
        source_platform = self._clean_source_value(source_platform)
        source_url = self._clean_source_value(source_url)
        if source_url:
            source_url = urldefrag(source_url)[0]
        external_id = self._clean_source_value(external_id) or source_url
        now = _now()
        metadata = {
            "source_platform": source_platform, "external_id": external_id,
            "source_url": source_url, "origin": origin or "unknown",
            "published_at": published_at or "",
        }
        values.update({key: value for key, value in metadata.items() if value not in (None, "", "unknown")})
        data = {**defaults, **metadata, **values, "created_at": now, "updated_at": now,
                "fetched_at": now if fetched_at is None else fetched_at}
        with self._write_transaction():
            rows = self.conn.execute(
                f"SELECT * FROM {table} WHERE source_platform=? AND source_platform <> '' AND "
                "((external_id=? AND external_id <> '') OR (source_url=? AND source_url <> ''))",
                (source_platform, external_id, source_url),
            ).fetchall()
            if len(rows) > 1:
                raise ValueError("conflicting source identities")
            row = rows[0] if rows else None
            changed = row is None or any(row[key] != value for key, value in values.items())
            updates = [f"{key}=excluded.{key}" for key in values]
            updates.append("fetched_at=excluded.fetched_at")
            if changed:
                updates.append("updated_at=excluded.updated_at")
            self.conn.execute(
                f"INSERT INTO {table}({', '.join(data)}) VALUES({', '.join('?' for _ in data)}) "
                f"ON CONFLICT DO UPDATE SET {', '.join(updates)}",
                tuple(data.values()),
            )
            return "inserted" if row is None else ("updated" if changed else "skipped")

    def _upsert_project_result(
        self, name: str, full_name: str, stars: int, language: str,
        description: str, tags: list[str], tech: str = "", *,
        external_id: str | None = None, source_platform: str | None = None,
        source_url: str | None = None, origin: str = "unknown",
        published_at: str | None = None, fetched_at: float | None = None,
    ) -> str:
        return self._upsert_source_result(
            "projects",
            {"name": name, "full_name": full_name, "stars": self._fmt_stars(stars),
             "stars_int": stars, "language": language, "description": description or "",
             "tags": json.dumps(tags, ensure_ascii=False), "tech": tech,
             "score": 0, "rank": "", "delta": "", "delta_int": 0, "reason": ""},
            {"logo": "GH", "logo_class": "l-gh"},
            source_platform="github" if source_platform is None else source_platform,
            external_id=full_name if external_id is None else external_id,
            source_url=source_url, origin=origin, published_at=published_at, fetched_at=fetched_at,
        )

    def upsert_project(self, name: str, full_name: str, stars: int, language: str,
                       description: str, tags: list[str], tech: str = "", **kwargs: Any) -> bool:
        """Return True only for an insert; source metadata is accepted as keyword arguments."""
        return self._upsert_project_result(
            name, full_name, stars, language, description, tags, tech, **kwargs
        ) == "inserted"

    def _upsert_news_result(
        self, title: str, source: str, summary: str, url: str = "", *,
        external_id: str | None = None, source_platform: str | None = None,
        source_url: str | None = None, origin: str = "unknown",
        published_at: str | None = None, fetched_at: float | None = None,
    ) -> str:
        return self._upsert_source_result(
            "news", {"title": title, "source": source, "summary": summary or "",
                     "importance": 0, "insight": ""},
            {"source_logo": "N", "source_class": "l-blue", "category": "AI", "heat": ""},
            source_platform=source if source_platform is None else source_platform,
            external_id=external_id, source_url=source_url or url,
            origin=origin, published_at=published_at, fetched_at=fetched_at,
        )

    def upsert_news(self, title: str, source: str, summary: str, url: str = "", **kwargs: Any) -> bool:
        return self._upsert_news_result(title, source, summary, url, **kwargs) == "inserted"

    def _upsert_job_result(
        self, title: str, city: str, meta: str, match: int | None,
        tags: list[str], reason: str, *, external_id: str | None = None,
        source_platform: str | None = None, source_url: str | None = None,
        origin: str = "unknown", published_at: str | None = None,
        fetched_at: float | None = None, company: str | None = None,
        direction: str | None = None, grad_year: str | None = None, salary: str | None = None,
    ) -> str:
        values = {
            "title": title, "city": city or "", "meta": meta or "", "match": int(match or 0),
            "tags": json.dumps(tags, ensure_ascii=False), "reason": reason or "",
        }
        values.update({key: value for key, value in (
            ("company", company), ("direction", direction), ("grad_year", grad_year), ("salary", salary),
        ) if value is not None})
        return self._upsert_source_result(
            "jobs", values,
            {"company": "", "logo": "", "logo_class": "", "direction": "",
             "grad_year": "", "salary": "", "is_referral": 0, "source_logo": ""},
            external_id=external_id, source_platform=source_platform, source_url=source_url,
            origin=origin, published_at=published_at, fetched_at=fetched_at,
        )

    def upsert_job(self, title: str, city: str, meta: str, match: int,
                   tags: list[str], reason: str, *, external_id: str | None = None,
                   source_platform: str | None = None, source_url: str | None = None,
                   origin: str = "unknown", published_at: str | None = None, **kwargs: Any) -> bool:
        return self._upsert_job_result(
            title, city, meta, match, tags, reason,
            external_id=external_id, source_platform=source_platform, source_url=source_url,
            origin=origin, published_at=published_at, **kwargs,
        ) == "inserted"

    @staticmethod
    def _fmt_stars(stars: int) -> str:
        if stars >= 1000:
            return f"{stars / 1000:.1f}k"
        return str(stars)
