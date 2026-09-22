"""本地开发起后端。

默认是 real 模式：数据落在 ``runs/``，不自动灌演示数据；启动前必须提供至少
32 位 ``JWT_KEY``。体验设计稿使用 ``APP_MODE=demo``，数据隔离到 ``runs/demo``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, "src")

import uvicorn

from agent_kernel.adapters.api.app import create_app
from agent_kernel.adapters.auth_store import AuthSettings, SqliteUserStore
from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.platform_store import PlatformStore

def build_app():
    mode = os.getenv("APP_MODE", "real").strip().lower()
    if mode not in {"demo", "real"}:
        raise RuntimeError("APP_MODE must be demo or real")
    data_dir = Path(os.getenv("APP_DATA_DIR", "runs/demo" if mode == "demo" else "runs"))
    settings = AuthSettings.from_env(mode)
    data_dir.mkdir(parents=True, exist_ok=True)
    content_store = SqliteContentStore(str(data_dir / "content.db"))
    user_store = SqliteUserStore(str(data_dir / "users.db"))
    platform_store = PlatformStore(str(data_dir / "platform.db"))
    if mode == "demo" and not platform_store.list_jobs():
        platform_store.seed_demo()
    app = create_app(
        content_store,
        user_store=user_store,
        auth_settings=settings,
        platform_store=platform_store,
        enable_scheduler=os.getenv("ENABLE_SCHEDULER", "0").strip() == "1",
    )
    return app


def main() -> None:
    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.getenv("PORT", "8010")))

if __name__ == "__main__":
    main()
