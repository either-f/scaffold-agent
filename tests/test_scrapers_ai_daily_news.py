"""AI 日报模块采集层离线测试：假 http_get 验证 aihot/x_kol 解析正确，
以及 SqliteContentStore.upsert 去重。不打真实网络。

运行：PYTHONPATH=src python3 tests/test_scrapers_ai_daily_news.py   （也兼容 pytest）
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.pop("X_BEARER_TOKEN", None)  # 测试环境变量隔离，别让宿主机真实配置污染断言

from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.scrapers.aihot import AiHotScraper
from agent_kernel.adapters.scrapers.x_kol import XKolScraper

AIHOT_HTML = """
<div class="news-card">
  <a href="/news/1">
    <h3>Claude 5 发布，支持 200 万 token 上下文</h3>
    <p>Anthropic 今日发布新一代模型...</p>
  </a>
</div>
<div class="news-card">
  <a href="/news/2">
    <h3>某厂发布新款笔记本</h3>
    <p>跟 AI 无关的一条新闻...</p>
  </a>
</div>
"""


def _fake_http_get(html: str):
    return lambda url: html


def test_aihot_scraper_parses_items():
    scraper = AiHotScraper(http_get=_fake_http_get(AIHOT_HTML))
    items = scraper.fetch({})
    assert len(items) == 2
    item = items[0]
    assert item.source_platform == "aihot"
    assert item.title == "Claude 5 发布，支持 200 万 token 上下文"
    assert item.url == "https://www.aibase.com/news/1"
    assert "Anthropic" in item.structured["summary"]


def test_aihot_scraper_respects_limit():
    scraper = AiHotScraper(http_get=_fake_http_get(AIHOT_HTML))
    items = scraper.fetch({"limit": 1})
    assert len(items) == 1


def _fake_x_http_get(username_to_id: dict, tweets_by_id: dict):
    def _get(url: str, headers: dict):
        assert headers["Authorization"] == "Bearer fake-token"
        if "/tweets" in url:
            user_id = url.split("/users/")[1].split("/")[0]
            return {"data": tweets_by_id.get(user_id, [])}
        username = url.rsplit("/", 1)[-1]
        return {"data": {"id": username_to_id[username]}}

    return _get


def test_x_kol_scraper_flags_model_updates():
    http_get = _fake_x_http_get(
        username_to_id={"AnthropicAI": "111"},
        tweets_by_id={
            "111": [
                {"id": "t1", "text": "We just released a new model update", "created_at": "2026-08-12"},
                {"id": "t2", "text": "Happy Tuesday everyone", "created_at": "2026-08-11"},
            ]
        },
    )
    scraper = XKolScraper(accounts=["AnthropicAI"], bearer_token="fake-token", http_get=http_get)
    items = scraper.fetch({})
    assert len(items) == 2
    assert items[0].structured["is_model_update"] is True
    assert items[1].structured["is_model_update"] is False
    assert items[0].structured["author"] == "AnthropicAI"


def test_x_kol_scraper_requires_token():
    scraper = XKolScraper(accounts=["AnthropicAI"], bearer_token=None, http_get=lambda u, h: {})
    try:
        scraper.fetch({})
        assert False, "应在缺少 bearer_token 时抛错"
    except RuntimeError as exc:
        assert "X_BEARER_TOKEN" in str(exc)


def test_content_store_dedup_across_sources():
    store = SqliteContentStore(":memory:")
    scraper = AiHotScraper(http_get=_fake_http_get(AIHOT_HTML))
    items = scraper.fetch({})

    first_id = store.upsert("ai-daily-news", items[0])
    second_id = store.upsert("ai-daily-news", items[0])

    assert first_id is not None
    assert second_id is None
    assert len(store.list_items(module="ai-daily-news")) == 1


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok: {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    run_all()
