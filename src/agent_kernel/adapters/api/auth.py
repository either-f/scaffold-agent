"""注册/登录 REST 层：对 AuthManager 的薄转发，不含业务逻辑。

对齐 blog-backend 的 `ResponseResult` 契约——所有业务响应都是 HTTP 200 +
`{"code", "msg", "data"}`，错误码沿用 `RespEnum`（1001 密码错 / 1002 未登录 /
1003 无权限 / 1005 验证码错 / 1006 用户名或邮箱已存在），前端按 `code` 判断，
不按 HTTP 状态码。跟 `app.py` 的 `create_app(store)` 一样是工厂函数，路由挂在
传入的 `AuthManager` 上，测试用内存库隔离。

接口对照（blog-backend -> 本文件）：

- `GET /public/ask-code`        <- PublicController.askVerifyCode
- `POST /user/register`         <- UserController.register
- `POST /user/reset-confirm`    <- UserController.resetConfirm
- `POST /user/reset-password`   <- UserController.resetPassword
- `POST /user/login`            <- SecurityHandler.onAuthenticationSuccess（原为表单登录）
- `GET  /user/auth/info`        <- UserController.getInfo
- `POST /user/logout`           <- SecurityHandler.onLogoutSuccess

差异（ponytail 边界，都在这台无 Redis/RabbitMQ/MySQL 的机器上做的取舍）：
- 登录原版是 Spring Security 表单登录（`application/x-www-form-urlencoded`），
  这里改成 JSON `{"username","password"}`，username 也接受邮箱；
- 角色/权限（RBAC 六张表）不随注册登录移植，`/user/auth/info` 的 roles/permissions
  固定返回空列表，token 里也不再放 authorities claim；
- 验证码发送原版走 RabbitMQ → 邮件，这里默认只打印日志，`AuthSettings.dev_mode`
  下回显 `dev_code` 便于联调，真实 SMTP 通过 `AuthSettings.send_email` 钩子接入。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator

from ..auth_store import (
    AuthManager,
    VERIFY_TYPES,
    VERIFY_TYPE_REGISTER,
    VERIFY_TYPE_RESET,
)

# ---- ResponseResult 契约 -------------------------------------------------
SUCCESS = 200
FAILURE = 500
USERNAME_OR_PASSWORD_ERROR = 1001
NOT_LOGIN = 1002
NO_PERMISSION = 1003
REQUEST_FREQUENTLY = 1004
VERIFY_CODE_ERROR = 1005
USERNAME_OR_EMAIL_EXIST = 1006
PARAM_ERROR = 1007
AUTH_NOT_CONFIGURED = 1013
BLACK_LIST_ERROR = 1012

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9\u4e00-\u9fa5]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ok(data=None, msg: str = "success") -> dict:
    return {"code": SUCCESS, "msg": msg, "data": data}


def err(code: int, msg: str) -> dict:
    return {"code": code, "msg": msg, "data": None}


def _verify_code_error(stored: str | None, code: str) -> dict | None:
    """把 verifyCode 的两种失败映射成响应；成功返回 None。"""
    if stored is None:
        return err(VERIFY_CODE_ERROR, "请先获取验证码")
    if stored != code:
        return err(VERIFY_CODE_ERROR, "验证码错误")
    return None


def _validate_email(v: str) -> str:
    if len(v) < 4 or not EMAIL_PATTERN.match(v):
        raise ValueError("邮箱格式错误")
    return v


def _validate_code(v: str) -> str:
    if len(v) != 6 or not v.isdigit():
        raise ValueError("验证码格式错误")
    return v


def _validate_username(v: str) -> str:
    if not (1 <= len(v) <= 10) or not USERNAME_PATTERN.match(v):
        raise ValueError("用户名格式错误")
    return v


def _validate_password(v: str) -> str:
    if not (6 <= len(v) <= 20):
        raise ValueError("密码长度需在 6-20 位")
    return v


def _user_view(user: dict) -> dict:
    """User -> 前台可见字段（等价 asViewObject(UserAccountVO.class) 的公开子集）。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "nickname": user["nickname"],
        "email": user["email"],
        "avatar": user["avatar"],
        "intro": user["intro"],
        "gender": user["gender"],
        "roles": [],
        "permissions": [],
    }


# ---- 请求体（等价各 DTO 的 @Valid 约束） ----------------------------------
class RegisterBody(BaseModel):
    username: str
    password: str
    code: str
    email: str

    _v_username = field_validator("username")(_validate_username)
    _v_password = field_validator("password")(_validate_password)
    _v_code = field_validator("code")(_validate_code)
    _v_email = field_validator("email")(_validate_email)


class ResetConfirmBody(BaseModel):
    code: str
    email: str

    _v_code = field_validator("code")(_validate_code)
    _v_email = field_validator("email")(_validate_email)


class ResetPasswordBody(BaseModel):
    password: str
    code: str
    email: str

    _v_password = field_validator("password")(_validate_password)
    _v_code = field_validator("code")(_validate_code)
    _v_email = field_validator("email")(_validate_email)


class LoginBody(BaseModel):
    username: str
    password: str


# ---- 路由工厂 -------------------------------------------------------------
def create_auth_router(manager: AuthManager) -> APIRouter:
    router = APIRouter()

    def current_user(request: Request) -> dict | None:
        header = request.headers.get("Authorization")
        return manager.resolve_token(header)

    @router.get("/public/ask-code")
    def ask_code(email: str, type: str) -> dict:
        if type not in VERIFY_TYPES:
            return err(PARAM_ERROR, "邮箱类型错误")
        if len(email) < 4 or not EMAIL_PATTERN.match(email):
            return err(PARAM_ERROR, "邮箱格式错误")
        if not manager.settings.dev_mode and manager.settings.send_email is None:
            return err(AUTH_NOT_CONFIGURED, "真实模式尚未配置邮件发送器")
        code = manager.ask_code(type, email)
        resp = ok("验证码已发送，请注意查收！")
        if manager.settings.dev_mode:
            resp["dev_code"] = code
        return resp

    @router.post("/user/register")
    def register(body: RegisterBody) -> dict:
        stored = manager.store.get_verify_code(VERIFY_TYPE_REGISTER, body.email)
        invalid = _verify_code_error(stored, body.code)
        if invalid is not None:
            return invalid
        if manager.store.user_exists(body.username, body.email):
            return err(USERNAME_OR_EMAIL_EXIST, "用户名或邮箱已存在")
        manager.register(body.username, body.password, body.email)
        manager.store.delete_verify_code(VERIFY_TYPE_REGISTER, body.email)
        return ok()

    @router.post("/user/reset-confirm")
    def reset_confirm(body: ResetConfirmBody) -> dict:
        stored = manager.store.get_verify_code(VERIFY_TYPE_RESET, body.email)
        invalid = _verify_code_error(stored, body.code)
        if invalid is not None:
            return invalid
        return ok()

    @router.post("/user/reset-password")
    def reset_password(body: ResetPasswordBody) -> dict:
        stored = manager.store.get_verify_code(VERIFY_TYPE_RESET, body.email)
        invalid = _verify_code_error(stored, body.code)
        if invalid is not None:
            return invalid
        if not manager.reset_password(body.email, body.password):
            return err(FAILURE, "failure")
        manager.store.delete_verify_code(VERIFY_TYPE_RESET, body.email)
        return ok()

    @router.post("/user/login")
    def login(body: LoginBody) -> dict:
        user = manager.authenticate(body.username, body.password)
        if user is None:
            return err(USERNAME_OR_PASSWORD_ERROR, "用户名或密码错误")
        if user.get("_disabled"):
            return err(BLACK_LIST_ERROR, "账号被封禁")
        token_info = manager.issue_token(user)
        view = _user_view(user)
        view.update(token_info)
        return ok(view, "登录成功")

    @router.get("/user/auth/info")
    def get_info(user: dict | None = Depends(current_user)) -> dict:
        if user is None:
            return err(NOT_LOGIN, "请先登录")
        record = manager.store.find_user_by_id(user["id"])
        if record is None:
            return err(NOT_LOGIN, "请先登录")
        return ok(_user_view(record))

    @router.post("/user/logout")
    def logout(request: Request) -> dict:
        if manager.invalidate_token(request.headers.get("Authorization")):
            return ok()
        return err(NOT_LOGIN, "请先登录")

    return router
