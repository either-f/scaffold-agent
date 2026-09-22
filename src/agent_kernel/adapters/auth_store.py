"""用户注册/登录的存储与鉴权原语：sqlite 单文件 + bcrypt + JWT。

对齐 blog-backend（Java Spring Security）的注册/登录语义，但把 MySQL/Redis/
RabbitMQ 三层依赖收拢成本项目惯用的「sqlite 单文件 + 零外部服务」：

- `users` 表对应 `sys_user`，注册默认值（nickname=username、register_type=0、
  gender/avatar/intro/is_deleted 默认）与 `UserServiceImpl.userRegister` 一致；
- `verify_codes` 表对应 Redis 的 `verifyCode:{type}:{email}`，6 位数字、TTL 可配；
- `jwt_whitelist` 表对应 Redis 的 JWT 白名单 `jwt:whitelist:{jti}`，登出即删除；
- 密码哈希用 BCrypt（等价 Spring `BCryptPasswordEncoder`）；
- 令牌用 HMAC256 JWT（jti + id + name，`Bearer ` 前缀，白名单 + 过期双重校验）。

第三方依赖 `bcrypt` / `PyJWT` 只在 adapter 层引用，符合「内核零第三方依赖、
adapter 才引依赖」的扩展纪律（见 README「扩展纪律」）。

ponytail: 原版 token 白名单/验证码都存在 Redis，天然支持多进程与分布式；本实现
用 sqlite 单文件，只覆盖单进程本地部署。要上多实例，把三个表的读写换成 Redis/
pgvector 即可，`AuthManager` 的方法签名不变。
"""
from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import bcrypt
import jwt

# 注册方式：0=邮箱/账号（跟 RegisterOrLoginTypeEnum.EMAIL.getRegisterType() 对齐）
REGISTER_TYPE_EMAIL = 0
# 默认头像/简介/性别（对齐 UserConst.DEFAULT_AVATAR / DEFAULT_INTRODUCTION / DEFAULT_GENDER）
DEFAULT_GENDER = 0
DEFAULT_AVATAR = ""
DEFAULT_INTRO = ""

# 验证码类型（对齐 PublicController.ask-code 的 type 正则 register|reset|resetEmail）
VERIFY_TYPE_REGISTER = "register"
VERIFY_TYPE_RESET = "reset"
VERIFY_TYPE_RESET_EMAIL = "resetEmail"
VERIFY_TYPES = (VERIFY_TYPE_REGISTER, VERIFY_TYPE_RESET, VERIFY_TYPE_RESET_EMAIL)

EmailSender = Callable[[str, str, str], None]  # (type, email, code) -> None


def _now() -> float:
    return time.time()


@dataclass
class AuthSettings:
    """鉴权可配置项，全部可被环境/调用方覆盖（对齐 application.yml 的 spring.security.jwt）。"""

    jwt_key: str = "jwt-key"            # 对应 ${JWT_KEY:jwt-key}
    jwt_expire_days: int = 7            # 对应 spring.security.jwt.expire=7（天）
    verify_code_ttl_seconds: int = 300  # 对应 RedisConst.VERIFY_CODE_EXPIRATION=5（分钟）
    dev_mode: bool = True               # dev 模式：验证码回显 + 打日志，便于无 SMTP 联调
    send_email: EmailSender | None = None  # 真实邮件发送钩子，默认 None 只打印日志
    platform_owner_id: int | None = None

    @classmethod
    def from_env(cls, app_mode: str | None = None) -> "AuthSettings":
        """读取启动配置；测试和工厂调用仍可直接构造 AuthSettings。"""
        mode = (app_mode or os.getenv("APP_MODE", "real")).strip().lower()
        if mode not in {"demo", "real"}:
            raise ValueError("APP_MODE must be demo or real")
        jwt_key = os.getenv("JWT_KEY", "")
        if mode == "real" and len(jwt_key) < 32:
            raise ValueError("real mode requires JWT_KEY with at least 32 characters")
        if mode == "demo" and not jwt_key:
            jwt_key = "demo-only-key-change-before-real-mode-32chars"
        owner_text = os.getenv("PLATFORM_OWNER_ID", "").strip()
        owner_id = int(owner_text) if owner_text else None
        return cls(
            jwt_key=jwt_key,
            dev_mode=mode == "demo",
            emit_code_log=mode == "demo",
            platform_owner_id=owner_id,
        )

    emit_code_log: bool = True


@dataclass
class AuthManager:
    """把 SqliteUserStore 的持久化原语组合成注册/登录/重置/令牌生命周期。

    拆成独立类是为了让 FastAPI 路由层（adapters/api/auth.py）保持薄：路由只做
    参数校验 + 调这里的方法 + 包 ResponseResult 信封，业务语义都在这里，等价
    blog-backend 的 `UserServiceImpl` + `JwtUtils`。
    """

    store: "SqliteUserStore"
    settings: AuthSettings = field(default_factory=AuthSettings)

    # ---- 密码（bcrypt） ---------------------------------------------------
    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify_password(self, password: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    # ---- 验证码 -----------------------------------------------------------
    def generate_verify_code(self) -> str:
        # 对齐 `(int)((Math.random()*9+1)*100000)`：100000..999999 的 6 位数字
        return str(secrets.randbelow(900000) + 100000)

    def ask_code(self, type_: str, email: str) -> str:
        code = self.generate_verify_code()
        self.store.set_verify_code(type_, email, code, self.settings.verify_code_ttl_seconds)
        if self.settings.send_email is not None:
            self.settings.send_email(type_, email, code)
        elif self.settings.dev_mode and self.settings.emit_code_log:
            # 仅显式 demo/测试模式打印；真实模式由路由在调用前拒绝未配置发送器
            print(f"[auth] verify code for {type_}@{email}: {code}")
        return code

    # ---- 令牌（JWT） ------------------------------------------------------
    def issue_token(self, user: dict) -> dict:
        jti = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=self.settings.jwt_expire_days)
        payload = {
            "jti": jti,
            "id": user["id"],
            "name": user["username"],
            "iat": now,
            "exp": expire,
        }
        token = jwt.encode(payload, self.settings.jwt_key, algorithm="HS256")
        self.store.add_token(jti, token, expire.timestamp())
        # expire 用毫秒时间戳（对齐 fastjson 序列化 Date 的默认行为）
        return {"token": token, "expire": int(expire.timestamp() * 1000)}

    def resolve_token(self, header: str | None) -> dict | None:
        token = self._convert_token(header)
        if token is None:
            return None
        try:
            payload = jwt.decode(token, self.settings.jwt_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        jti = payload.get("jti")
        if not self.store.token_is_valid(jti, token):
            return None
        return {"id": payload.get("id"), "name": payload.get("name"), "jti": jti}

    def invalidate_token(self, header: str | None) -> bool:
        token = self._convert_token(header)
        if token is None:
            return False
        try:
            payload = jwt.decode(token, self.settings.jwt_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            return False
        return self.store.delete_token(payload.get("jti"))

    @staticmethod
    def _convert_token(header: str | None) -> str | None:
        if not header or not header.startswith("Bearer "):
            return None
        return header[len("Bearer "):]

    # ---- 业务语义（等价 UserServiceImpl） ---------------------------------
    def register(self, username: str, password: str, email: str) -> None:
        self.store.create_user(
            username=username,
            nickname=username,
            password=self.hash_password(password),
            email=email,
        )

    def reset_password(self, email: str, password: str) -> bool:
        return self.store.update_password(email, self.hash_password(password))

    def authenticate(self, account: str, password: str) -> dict | None:
        """account 可为用户名或邮箱（对齐 findAccountByNameOrEmail）。"""
        user = self.store.find_user_by_name_or_email(account)
        if user is None:
            return None
        if not self.verify_password(password, user["password"]):
            return None
        if user["is_disable"] == 1:
            # 复用「账号被封禁」语义，由路由映射为具体错误码
            user["_disabled"] = True
            return user
        return user


class SqliteUserStore:
    """用户/验证码/令牌白名单三张表的 sqlite 持久化。

    同一套 sqlite 单文件模式，跟 SqliteContentStore / SqliteMemory 一致；
    `check_same_thread=False` 因为 FastAPI 同步路由跑在线程池。
    """

    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS users("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "nickname TEXT NOT NULL, "
            "username TEXT NOT NULL UNIQUE, "
            "password TEXT NOT NULL, "
            "gender INTEGER NOT NULL DEFAULT 0, "
            "avatar TEXT NOT NULL DEFAULT '', "
            "intro TEXT NOT NULL DEFAULT '', "
            "email TEXT, "
            "register_type INTEGER NOT NULL DEFAULT 0, "
            "register_ip TEXT, "
            "register_address TEXT, "
            "login_type INTEGER, "
            "login_ip TEXT, "
            "login_address TEXT, "
            "is_disable INTEGER NOT NULL DEFAULT 0, "
            "login_time REAL, "
            "create_time REAL, "
            "update_time REAL, "
            "is_deleted INTEGER NOT NULL DEFAULT 0)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS verify_codes("
            "type TEXT NOT NULL, "
            "email TEXT NOT NULL, "
            "code TEXT NOT NULL, "
            "expires_at REAL NOT NULL, "
            "PRIMARY KEY(type, email))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS jwt_whitelist("
            "jti TEXT PRIMARY KEY, "
            "token TEXT NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        self.conn.commit()

    # ---- users ------------------------------------------------------------
    def create_user(self, username: str, nickname: str, password: str, email: str) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO users(username, nickname, password, email, gender, avatar, intro, "
            "register_type, is_deleted, login_time, create_time, update_time) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                username, nickname, password, email, DEFAULT_GENDER, DEFAULT_AVATAR,
                DEFAULT_INTRO, REGISTER_TYPE_EMAIL, 0, now, now, now,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def user_exists(self, username: str | None, email: str | None) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM users WHERE (username=? OR email=?) AND is_deleted=0 LIMIT 1",
            (username, email),
        ).fetchone()
        return row is not None

    def find_user_by_name_or_email(self, text: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE (username=? OR email=?) AND is_deleted=0 LIMIT 1",
            (text, text),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_user_by_id(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id=? AND is_deleted=0 LIMIT 1", (user_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def update_password(self, email: str, password: str) -> bool:
        cur = self.conn.execute(
            "UPDATE users SET password=?, update_time=? WHERE email=? AND is_deleted=0",
            (password, _now(), email),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- verify_codes -----------------------------------------------------
    def set_verify_code(self, type_: str, email: str, code: str, ttl_seconds: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verify_codes(type, email, code, expires_at) VALUES(?,?,?,?)",
            (type_, email, code, _now() + ttl_seconds),
        )
        self.conn.commit()

    def get_verify_code(self, type_: str, email: str) -> str | None:
        row = self.conn.execute(
            "SELECT code, expires_at FROM verify_codes WHERE type=? AND email=?",
            (type_, email),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] < _now():
            self.delete_verify_code(type_, email)
            return None
        return row["code"]

    def delete_verify_code(self, type_: str, email: str) -> None:
        self.conn.execute(
            "DELETE FROM verify_codes WHERE type=? AND email=?", (type_, email),
        )
        self.conn.commit()

    # ---- jwt_whitelist ----------------------------------------------------
    def add_token(self, jti: str, token: str, expires_at: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO jwt_whitelist(jti, token, expires_at) VALUES(?,?,?)",
            (jti, token, expires_at),
        )
        self.conn.commit()

    def token_is_valid(self, jti: str, token: str) -> bool:
        row = self.conn.execute(
            "SELECT token, expires_at FROM jwt_whitelist WHERE jti=?", (jti,),
        ).fetchone()
        if row is None or row["token"] != token:
            return False
        if row["expires_at"] < _now():
            self.delete_token(jti)
            return False
        return True

    def delete_token(self, jti: str) -> bool:
        cur = self.conn.execute("DELETE FROM jwt_whitelist WHERE jti=?", (jti,))
        self.conn.commit()
        return cur.rowcount > 0
