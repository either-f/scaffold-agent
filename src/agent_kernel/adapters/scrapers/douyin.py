"""抖音 内推视频/图文搜索抓取。

# ponytail: 未验证真实反爬绕过与登录态。抖音搜索页高度依赖客户端渲染 JS 与签名参数，
# 纯 HTML 解析大概率拿不到真实内容——比 xhs/zhihu 更需要走官方 API 或无头浏览器渲染，
# 此文件只提供跟其它 scraper 一致的接口骨架与选择器占位，标注为最需要重做的一个，
# 接入生产前优先评估用 Playwright 渲染后再解析。
"""
from __future__ import annotations

from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str, dict[str, Any]], str]

SEARCH_URL = "https://www.douyin.com/search"


def _default_http_get(url: str, params: dict[str, Any]) -> str:
    import requests

    resp = requests.get(
        url, params=params, timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.text


class DouyinScraper:
    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        html = self._http_get(f"{SEARCH_URL}/{query.get('keyword', '内推')}", {})
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for card in soup.select(".search-item"):
            link_el = card.select_one("a")
            item_id = (link_el["href"].rstrip("/").split("/")[-1]) if link_el else ""
            title_el = card.select_one(".title")
            author_el = card.select_one(".author-name")
            url = link_el["href"] if link_el else ""
            items.append(RawItem(
                external_id=str(item_id),
                source_platform="douyin",
                url=url if url.startswith("http") else f"https://www.douyin.com{url}",
                title=title_el.get_text(strip=True) if title_el else "",
                raw_text=card.get_text(" ", strip=True),
                structured={
                    "author": author_el.get_text(strip=True) if author_el else "",
                    "platform": "抖音",
                    "referral_company": query.get("company", ""),
                },
            ))
        return items
