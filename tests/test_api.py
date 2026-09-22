"""离线单测：FastAPI TestClient + 内存版 SqliteContentStore，不打真实网络。

运行：PYTHONPATH=src python3 -m pytest tests/test_api.py   （也兼容直接 pytest）
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_kernel.adapters.api.app import create_app
from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.scrapers import RawItem


def make_client() -> TestClient:
    store = SqliteContentStore(":memory:")
    store.upsert("job-hunter", RawItem(
        external_id="job-1", source_platform="boss", url="https://boss.example/1",
        title="后端工程师", raw_text="...", structured={"salary": "20-30K"}, score=5.0,
    ))
    store.upsert("github-trending", RawItem(
        external_id="repo-1", source_platform="ghfind", url="https://github.com/a/b",
        title="a/b", raw_text="...", structured={"stars": 100}, score=1.0,
    ))
    return TestClient(create_app(store))


def test_list_modules():
    client = make_client()
    resp = client.get("/api/modules")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()}
    assert ids == {"job-hunter", "github-trending", "ai-daily-news"}
    job = next(m for m in resp.json() if m["id"] == "job-hunter")
    assert job["has_approval"] is True


def test_list_items_filters_by_module_and_status():
    client = make_client()
    resp = client.get("/api/items", params={"module": "job-hunter"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["title"] == "后端工程师"

    resp = client.get("/api/items", params={"module": "job-hunter", "status": "actioned"})
    assert resp.json() == []


def test_get_item_and_404():
    client = make_client()
    item_id = client.get("/api/items", params={"module": "job-hunter"}).json()[0]["id"]

    resp = client.get(f"/api/items/{item_id}")
    assert resp.status_code == 200
    assert resp.json()["source_platform"] == "boss"

    resp = client.get("/api/items/999999")
    assert resp.status_code == 404


def test_approve_changes_status_and_visible_in_list():
    client = make_client()
    item_id = client.get("/api/items", params={"module": "job-hunter"}).json()[0]["id"]

    resp = client.post(f"/api/items/{item_id}/approve", json={"run_id": "run-1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "actioned"

    resp = client.get("/api/items", params={"module": "job-hunter", "status": "actioned"})
    assert len(resp.json()) == 1

    resp = client.post("/api/items/999999/approve")
    assert resp.status_code == 404


def test_reject_changes_status():
    client = make_client()
    item_id = client.get("/api/items", params={"module": "github-trending"}).json()[0]["id"]

    resp = client.post(f"/api/items/{item_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
