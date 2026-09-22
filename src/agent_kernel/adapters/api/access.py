"""Shared authorization for platform administration and content review."""
from fastapi import HTTPException, Request

from ..auth_store import AuthManager


def require_owner(manager: AuthManager | None, request: Request) -> int | None:
    # The legacy no-auth factory is an offline/demo entry point only.
    if manager is None or manager.settings.dev_mode:
        return None
    user = manager.resolve_token(request.headers.get("Authorization"))
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if manager.settings.platform_owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="平台管理操作需要 owner 权限")
    return user["id"]
