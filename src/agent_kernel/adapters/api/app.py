"""HTTP 层：对 SqliteContentStore / module_registry 的薄转发，不含业务逻辑。

`create_app(store)` 是工厂函数而非模块级全局单例——测试用内存库隔离，
生产由调用方决定 db 路径，对齐 `sandbox_daytona.py` 的依赖注入风格。

注册/登录（blog-backend 移植）通过可选的 `user_store` 注入：传了 `SqliteUserStore`
就挂载 `auth.py` 的路由（`/public/ask-code`、`/user/*`），不传则完全不引入——
保持 `create_app(store)` 的向后兼容（既有内容 API 测试不依赖 bcrypt/PyJWT）。

平台页面（前端首页/招聘/项目/AI日报/自动化/数据源）通过可选的 `platform_store`
注入：传了 `PlatformStore` 就挂载 `platform.py` 的页面级聚合端点（`/api/platform/*`），
不传则不影响既有内容 API 与 auth。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ... import module_registry
from ..auth_store import AuthManager, AuthSettings, SqliteUserStore
from ..content_store import SqliteContentStore
from ..platform_store import PlatformStore
from .access import require_owner


class ActionBody(BaseModel):
    run_id: str | None = None
    payload: dict | None = None


def create_app(
    store: SqliteContentStore,
    user_store: SqliteUserStore | None = None,
    auth_settings: AuthSettings | None = None,
    platform_store: PlatformStore | None = None,
    enable_scheduler: bool = False,
) -> FastAPI:
    settings = auth_settings or AuthSettings()
    if not settings.dev_mode and (user_store is None or len(settings.jwt_key) < 32):
        raise ValueError("real mode requires user_store and a JWT key of at least 32 characters")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = None
        if platform_store is not None:
            from .collection_runtime import CollectionRuntime

            platform_store.interrupt_collection_runs()
            if not settings.dev_mode:
                platform_store.ensure_collection_automations([
                    {"id": m.id, "label": m.label, "schedule": m.schedule}
                    for m in module_registry.MODULES
                ])
            runtime = CollectionRuntime(platform_store, enable_scheduler and not settings.dev_mode)
            app.state.collection_runtime = runtime
        try:
            if runtime is not None:
                runtime.start()
            yield
        finally:
            if runtime is not None:
                await asyncio.to_thread(runtime.close)
                app.state.collection_runtime = None

    app = FastAPI(title="agent-kernel content API", lifespan=lifespan)
    app.state.collection_runtime = None
    app.state.auth_manager = None
    # ponytail: 本地开发放开跨域，前端另起端口跑 vite dev server；生产按需收紧 allow_origins。
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/api/modules")
    def list_modules() -> list[dict]:
        return [
            {"id": m.id, "label": m.label, "schedule": m.schedule, "has_approval": m.has_approval}
            for m in module_registry.MODULES
        ]

    @app.get("/api/items")
    def list_items(module: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        return [item.__dict__ for item in store.list_items(module=module, status=status, limit=limit)]

    @app.get("/api/items/{item_id}")
    def get_item(item_id: int) -> dict:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return item.__dict__

    @app.post("/api/items/{item_id}/approve")
    def approve_item(item_id: int, request: Request, body: ActionBody = ActionBody()) -> dict:
        require_owner(manager, request)
        if store.get(item_id) is None:
            raise HTTPException(status_code=404, detail="item not found")
        store.set_status(item_id, "actioned", "approve", run_id=body.run_id, payload=body.payload)
        return store.get(item_id).__dict__

    @app.post("/api/items/{item_id}/reject")
    def reject_item(item_id: int, request: Request, body: ActionBody = ActionBody()) -> dict:
        require_owner(manager, request)
        if store.get(item_id) is None:
            raise HTTPException(status_code=404, detail="item not found")
        store.set_status(item_id, "rejected", "reject", run_id=body.run_id, payload=body.payload)
        return store.get(item_id).__dict__

    manager: AuthManager | None = None
    if user_store is not None:
        from .auth import create_auth_router

        manager = AuthManager(store=user_store, settings=settings)
        app.state.auth_manager = manager
        app.include_router(create_auth_router(manager))

    if platform_store is not None:
        from .platform import create_platform_router
        from .personal import create_personal_router
        from ..personal_store import ensure_personal_schema

        ensure_personal_schema(platform_store)
        # 把 AuthManager 一并传给平台路由：写端点（收藏/投递等）用它解析 Bearer token
        app.include_router(create_platform_router(platform_store, auth_manager=manager))
        app.include_router(create_personal_router(platform_store, manager))

    return app
