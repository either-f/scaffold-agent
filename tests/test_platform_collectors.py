"""离线单测：平台采集器（爬虫脚本版）。

三个采集器都是真实网络抓取，单测不打网络：`parse_*` 解析函数是纯函数，喂本地
HTML fixture 验证解析；`collect_*` 用 monkeypatch 替换 `_fetch_html` 返回 fixture，
验证「HTML -> PlatformStore 写入」的完整链路与去重。这等价于 Fake 对照——真网络
只在 `POST /api/platform/collect/*` 手动触发时发生。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from agent_kernel.adapters import platform_collectors as collectors
from agent_kernel.adapters.platform_store import PlatformStore

GITHUB_HTML = """
<html><body>
<article class="Box-row">
  <h2><a href="/torvalds/linux">torvalds / linux</a></h2>
  <p>Linux kernel source tree</p>
  <a href="/torvalds/linux/stargazers">12.3k</a>
  <span itemprop="programmingLanguage">C</span>
</article>
<article class="Box-row">
  <h2><a href="/some/repo">some / repo</a></h2>
  <p>desc</p>
  <a href="/some/repo/stargazers">1,234</a>
  <span itemprop="programmingLanguage">Python</span>
</article>
</body></html>
"""

V2EX_HOT_JSON = [
    {"title": "OpenAI releases new agent model", "url": "https://example.com/ai-news", "content": ""},
    {"title": "How to bake bread", "url": "https://example.com/cooking", "content": ""},
    {"title": "Anthropic Claude LLM update", "url": "https://example.com/llm", "content": ""},
]

V2EX_HTML = """
<html><body>
<span class="item_title"><a href="/t/1">[北京] 某大厂招聘后端工程师</a></span>
<span class="item_title"><a href="/t/2">远程 | 创业公司招全栈</a></span>
<span class="item_title"><a href="/t/3">【上海】招 AI 算法工程师</a></span>
</body></html>
"""


def test_parse_github_trending():
    items = collectors.parse_github_trending(GITHUB_HTML)
    assert len(items) == 2
    assert items[0]["full_name"] == "torvalds/linux"
    assert items[0]["stars"] == 12300  # 12.3k
    assert items[1]["stars"] == 1234  # 1,234
    assert items[1]["language"] == "Python"


def test_parse_v2ex_hot_filters_by_keyword():
    items = collectors.parse_v2ex_hot(V2EX_HOT_JSON)
    assert [i["title"] for i in items] == [
        "OpenAI releases new agent model",
        "Anthropic Claude LLM update",
    ]
    assert items[0]["url"] == "https://example.com/ai-news"


def test_parse_v2ex_jobs_extracts_city():
    items = collectors.parse_v2ex_jobs(V2EX_HTML)
    assert [i["city"] for i in items] == ["北京", "远程", "上海"]


def test_collect_github_trending_writes_and_dedups(monkeypatch):
    store = PlatformStore(":memory:")
    monkeypatch.setattr(collectors, "_fetch_html", lambda url: GITHUB_HTML)
    assert collectors.collect_github_trending(store) == 2
    # 再采一次：按 full_name 去重，无新增
    assert collectors.collect_github_trending(store) == 0
    assert len(store.list_projects()) == 2


def test_collect_ai_news_writes(monkeypatch):
    store = PlatformStore(":memory:")
    monkeypatch.setattr(collectors, "_fetch_json", lambda url: V2EX_HOT_JSON)
    assert collectors.collect_ai_news(store) == 2
    news = store.list_news()
    assert all(n["source"] == "V2EX 热门" for n in news)


def test_collect_jobs_writes(monkeypatch):
    store = PlatformStore(":memory:")
    monkeypatch.setattr(collectors, "_fetch_html", lambda url: V2EX_HTML)
    assert collectors.collect_jobs(store) == 3
    jobs = store.list_jobs()
    assert len(jobs) == 3
    assert {j["city"] for j in jobs} == {"北京", "远程", "上海"}


def test_collectors_fail_gracefully(monkeypatch):
    store = PlatformStore(":memory:")

    def boom(url: str):
        raise ConnectionError("no network")

    monkeypatch.setattr(collectors, "_fetch_html", boom)
    monkeypatch.setattr(collectors, "_fetch_json", boom)
    assert collectors.collect_github_trending(store) == 0
    assert collectors.collect_ai_news(store) == 0
    assert collectors.collect_jobs(store) == 0


def test_collect_for_module_dispatch(monkeypatch):
    store = PlatformStore(":memory:")
    monkeypatch.setattr(collectors, "_fetch_html", lambda url: GITHUB_HTML)
    assert collectors.collect_for_module(store, "github-trending") == 2
    assert collectors.collect_for_module(store, "nope") == 0
