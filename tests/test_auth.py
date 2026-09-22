"""离线单测：注册/登录/重置密码的完整链路，内存库 + dev 回显验证码，不打真实网络/邮件。

运行：PYTHONPATH=src python3 -m pytest tests/test_auth.py
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

pytest.importorskip("fastapi")
pytest.importorskip("bcrypt")
pytest.importorskip("jwt")
from fastapi.testclient import TestClient

from agent_kernel.adapters.api.app import create_app
from agent_kernel.adapters.auth_store import AuthManager, AuthSettings, SqliteUserStore
from agent_kernel.adapters.content_store import SqliteContentStore


def make_client(dev_mode: bool = True) -> TestClient:
    store = SqliteContentStore(":memory:")
    user_store = SqliteUserStore(":memory:")
    settings = AuthSettings(dev_mode=dev_mode)
    return TestClient(create_app(store, user_store=user_store, auth_settings=settings))


def _ask_code(client: TestClient, type_: str, email: str) -> str:
    resp = client.get("/public/ask-code", params={"email": email, "type": type_})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200, body
    return body["dev_code"]


def test_register_login_info_logout_flow():
    client = make_client()
    email = "alice@example.com"
    code = _ask_code(client, "register", email)

    # 注册
    resp = client.post("/user/register", json={
        "username": "alice", "password": "secret123", "code": code, "email": email,
    })
    assert resp.json()["code"] == 200, resp.json()

    # 重复注册 -> 1006（原版先验验证码再查重，旧码已被消费，需用新码才会走到查重分支）
    code2 = _ask_code(client, "register", email)
    resp = client.post("/user/register", json={
        "username": "alice", "password": "secret123", "code": code2, "email": email,
    })
    assert resp.json()["code"] == 1006

    # 密码错误 -> 1001
    resp = client.post("/user/login", json={"username": "alice", "password": "wrong"})
    assert resp.json()["code"] == 1001

    # 正确登录，用户名和邮箱都可作 account
    resp = client.post("/user/login", json={"username": email, "password": "secret123"})
    assert resp.json()["code"] == 200
    data = resp.json()["data"]
    assert data["username"] == "alice"
    token = data["token"]
    assert data["expire"] > 0

    # 带 token 取当前用户信息
    resp = client.get("/user/auth/info", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["code"] == 200
    assert resp.json()["data"]["email"] == email

    # 无 token -> 1002
    resp = client.get("/user/auth/info")
    assert resp.json()["code"] == 1002

    # 登出后 token 失效
    resp = client.post("/user/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["code"] == 200
    resp = client.get("/user/auth/info", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["code"] == 1002


def test_register_requires_valid_code():
    client = make_client()
    resp = client.post("/user/register", json={
        "username": "bob", "password": "secret123", "code": "000000", "email": "bob@example.com",
    })
    assert resp.json()["code"] == 1005  # 未获取验证码


def test_reset_password_flow():
    client = make_client()
    email = "carol@example.com"
    code = _ask_code(client, "register", email)
    client.post("/user/register", json={
        "username": "carol", "password": "oldpass123", "code": code, "email": email,
    })

    # 重置确认
    reset_code = _ask_code(client, "reset", email)
    resp = client.post("/user/reset-confirm", json={"code": reset_code, "email": email})
    assert resp.json()["code"] == 200

    # 重置密码
    resp = client.post("/user/reset-password", json={
        "password": "newpass123", "code": reset_code, "email": email,
    })
    assert resp.json()["code"] == 200

    # 新密码可登录
    resp = client.post("/user/login", json={"username": "carol", "password": "newpass123"})
    assert resp.json()["code"] == 200


def test_ask_code_rejects_bad_type():
    client = make_client()
    resp = client.get("/public/ask-code", params={"email": "d@example.com", "type": "nope"})
    assert resp.json()["code"] == 1007
