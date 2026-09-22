"""离线单测：假 HTML 片段验证 GhfindScraper 解析逻辑，不打真实网络。"""
from __future__ import annotations

from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.scrapers.ghfind import GhfindScraper

FAKE_TRENDING_HTML = """
<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/owner-a/repo-a">owner-a / repo-a</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A fake trending repo for testing.</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/owner-a/repo-a/stargazers" class="Link--muted">1,234</a>
  <a href="/owner-a/repo-a/forks" class="Link--muted">56</a>
  <span class="d-inline-block float-sm-right">321 stars today</span>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/owner-b/repo-b">owner-b / repo-b</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Another fake repo.</p>
  <span itemprop="programmingLanguage">TypeScript</span>
  <a href="/owner-b/repo-b/stargazers" class="Link--muted">9,876</a>
  <a href="/owner-b/repo-b/forks" class="Link--muted">120</a>
  <span class="d-inline-block float-sm-right">55 stars today</span>
</article>
</body></html>
"""


def test_fetch_parses_repo_fields_from_fixture_html() -> None:
    scraper = GhfindScraper(http_get=lambda url: FAKE_TRENDING_HTML)

    items = scraper.fetch({"language": "python"})

    assert len(items) == 2
    first = items[0]
    assert first.external_id == "owner-a/repo-a"
    assert first.source_platform == "ghfind"
    assert first.url == "https://github.com/owner-a/repo-a"
    assert first.structured["language"] == "Python"
    assert first.structured["stars"] == 1234
    assert first.structured["today_stars"] == 321
    assert first.score == 321.0

    second = items[1]
    assert second.external_id == "owner-b/repo-b"
    assert second.structured["stars"] == 9876
    assert second.structured["today_stars"] == 55


def test_fetch_passes_language_and_since_into_url() -> None:
    seen_urls: list[str] = []

    def spy_http_get(url: str) -> str:
        seen_urls.append(url)
        return FAKE_TRENDING_HTML

    scraper = GhfindScraper(http_get=spy_http_get)
    scraper.fetch({"language": "rust", "since": "weekly"})

    assert seen_urls == ["https://github.com/trending/rust?since=weekly"]


def test_upsert_dedupes_same_repo_across_repeated_fetches() -> None:
    store = SqliteContentStore(":memory:")
    scraper = GhfindScraper(http_get=lambda url: FAKE_TRENDING_HTML)

    first_pass = scraper.fetch({})
    second_pass = scraper.fetch({})  # 模拟第二次抓取同一天榜单

    inserted_ids = [store.upsert("github-trending", item) for item in first_pass]
    reinserted_ids = [store.upsert("github-trending", item) for item in second_pass]

    assert all(i is not None for i in inserted_ids)
    assert all(i is None for i in reinserted_ids)  # 去重：第二次全部跳过
    assert len(store.list_items(module="github-trending")) == 2
