"""离线单测：平台页面端点（首页聚合流 + 五个模块页），内存库 + seed，不打真实网络。

运行：PYTHONPATH=src python3 -m pytest tests/test_platform_api.py
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_kernel.adapters.api.app import create_app
from agent_kernel.adapters.auth_store import SqliteUserStore
from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.platform_store import PlatformStore


def make_client() -> TestClient:
    store = SqliteContentStore(":memory:")
    platform = PlatformStore(":memory:")
    platform.seed_demo()
    user_store = SqliteUserStore(":memory:")
    return TestClient(create_app(store, user_store=user_store, platform_store=platform))


def test_home_aggregate():
    client = make_client()
    body = client.get("/api/platform/home").json()

    assert len(body["stats"]) == 4
    assert body["stats"][0]["title"] == "高匹配岗位"
    assert int(body["stats"][0]["num"]) >= 1

    kinds = [f["kind"] for f in body["feed"]]
    assert kinds == ["job", "project", "news", "referral"]
    assert body["feed"][0]["badge"]["text"] == "高匹配"
    assert body["feed"][2]["badge"]["variant"] == "blue"

    assert len(body["ranks"]) == 5
    assert body["ranks"][0]["title"].startswith("AGI")

    assert len(body["todos"]) == 3
    assert body["todos"][0]["has_dot"] is True

    assert [a["name"] for a in body["automations"]] == ["每日岗位订阅", "高匹配岗位提醒", "GitHub Agent 项目监控"]


def test_jobs_list_and_filters():
    client = make_client()

    all_jobs = client.get("/api/platform/jobs").json()["items"]
    assert len(all_jobs) == 7
    assert all_jobs[0]["match"] == "95%"  # 按匹配度倒序，小米 NLP 岗第一

    # 内推筛选
    referrals = client.get("/api/platform/jobs", params={"referral": "true"}).json()["items"]
    assert len(referrals) == 3
    assert all(j["referral"] for j in referrals)

    # 城市 + 匹配度筛选
    shenzhen = client.get("/api/platform/jobs", params={"city": "深圳", "match_min": 85}).json()["items"]
    assert len(shenzhen) >= 2
    assert all(j["match"] in ("92%", "89%", "88%", "95%", "86%") for j in shenzhen)

    # 侧边栏数据随端点返回
    body = client.get("/api/platform/jobs").json()
    assert len(body["follows"]) == 4
    assert len(body["status"]) == 4
    assert "字节跳动" in body["interview"]["time"]


def test_projects_and_star_filter():
    client = make_client()
    body = client.get("/api/platform/projects").json()

    items = body["items"]
    assert [p["name"] for p in items] == ["OpenHands", "LangGraph", "Milvus"]
    assert items[0]["meta"].startswith("All-Hands-AI/OpenHands")
    assert len(body["trend"]) == 7
    assert "MCP" in body["topics"]
    assert len(body["watches"]) == 3

    big = client.get("/api/platform/projects", params={"star": "10k"}).json()["items"]
    assert {p["name"] for p in big} == {"OpenHands", "LangGraph", "Milvus"}  # Milvus 23.4k 也 ≥ 10k


def test_news_automations_sources():
    client = make_client()

    news = client.get("/api/platform/news").json()
    assert [n["logo"] for n in news["items"]] == ["O", "A", "G"]
    assert news["items"][0]["score"] == "95"
    assert len(news["entities"]) == 4
    assert len(news["topics"]) == 5

    autos = client.get("/api/platform/automations").json()
    assert len(autos["rules"]) == 4
    assert autos["rules"][0]["flow"][0] == "定时触发"
    assert len(autos["logs"]) == 3
    assert autos["logs"][2]["error"] is True
    assert len(autos["pending"]) == 2
    assert len(autos["channels"]) == 3

    sources = client.get("/api/platform/sources").json()
    assert len(sources["items"]) == 6
    x = next(s for s in sources["items"] if s["name"] == "X / Twitter")
    assert x["status"] == "warn"
    assert x["action"] == "重新认证 ›"
    assert sources["metrics"][3]["error"] is True
    assert len(sources["alerts"]) == 2


def test_seed_is_idempotent():
    platform = PlatformStore(":memory:")
    platform.seed_demo()
    first = len(platform.list_jobs())
    platform.seed_demo()
    assert len(platform.list_jobs()) == first


def register_and_login(client: TestClient) -> str:
    email = "tester@example.com"
    ac = client.get("/public/ask-code", params={"email": email, "type": "register"}).json()
    client.post("/user/register", json={
        "username": "tester", "password": "secret123", "code": ac["dev_code"], "email": email,
    })
    login = client.post("/user/login", json={"username": "tester", "password": "secret123"}).json()
    return login["data"]["token"]


def test_write_actions_require_login():
    client = make_client()
    job_id = client.get("/api/platform/jobs").json()["items"][0]["id"]
    resp = client.post(f"/api/platform/jobs/{job_id}/favorite")
    assert resp.status_code == 401


def test_favorite_apply_flow():
    client = make_client()
    token = register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    job = client.get("/api/platform/jobs").json()["items"][0]
    job_id = job["id"]
    assert job["favorited"] is False

    resp = client.post(f"/api/platform/jobs/{job_id}/favorite", headers=headers)
    assert resp.json()["favorited"] is True
    # toggle 语义：再次点击取消收藏
    resp = client.post(f"/api/platform/jobs/{job_id}/favorite", headers=headers)
    assert resp.json()["favorited"] is False
    # 再收藏回来
    resp = client.post(f"/api/platform/jobs/{job_id}/favorite", headers=headers)
    assert resp.json()["favorited"] is True

    resp = client.post(f"/api/platform/jobs/{job_id}/apply", headers=headers)
    assert resp.json()["applied"] is True

    job = client.get("/api/platform/jobs", headers=headers).json()["items"][0]
    assert job["favorited"] is True
    assert job["applied"] is True


def test_toggle_todo_reconnect():
    client = make_client()

    autos = client.get("/api/platform/automations").json()["rules"]
    auto_id = autos[0]["id"]
    before = autos[0]["enabled"]
    resp = client.post(f"/api/platform/automations/{auto_id}/toggle")
    assert resp.json()["enabled"] is (not before)
    after = client.get("/api/platform/automations").json()["rules"][0]["enabled"]
    assert after is (not before)

    todo_id = client.get("/api/platform/home").json()["todos"][0]["id"]
    assert client.post(f"/api/platform/todos/{todo_id}/done").json()["done"] is True
    assert len(client.get("/api/platform/home").json()["todos"]) == 2

    x = next(s for s in client.get("/api/platform/sources").json()["items"] if s["status"] == "warn")
    assert client.post(f"/api/platform/sources/{x['id']}/reconnect").json()["reconnected"] is True
    x_after = next(s for s in client.get("/api/platform/sources").json()["items"] if s["id"] == x["id"])
    assert x_after["status"] == "ok"


def test_collect_endpoint_dispatches(monkeypatch):
    client = make_client()
    # 采集器是真实网络脚本，单测离线：monkeypatch 掉路由里的分派入口
    from agent_kernel.adapters.api import platform as platform_module

    monkeypatch.setattr(platform_module, "collect_for_module", lambda store, module_id: 5)
    resp = client.post("/api/platform/collect/job-hunter")
    assert resp.json() == {"module": "job-hunter", "collected": 5}
