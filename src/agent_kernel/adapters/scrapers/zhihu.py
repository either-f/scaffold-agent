"""知乎 内推文章/回答搜索抓取。

# ponytail: 未验证真实反爬绕过与登录态，同 xhs.py 的坦诚声明——选择器按公开搜索结果页
# 结构编写，接入生产前需用真实浏览器核对当前 DOM 结构。
"""
from __future__ import annotations

from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str, dict[str, Any]], str]

SEARCH_URL = "https://www.zhihu.com/search"


def _default_http_get(url: str, params: dict[str, Any]) -> str:
    import requests

    resp = requests.get(
        url, params=params, timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.text


class ZhihuScraper:
    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        html = self._http_get(SEARCH_URL, {"q": query.get("keyword", "内推"), "type": "content"})
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for card in soup.select(".SearchResult-Card"):
            link_el = card.select_one("a")
            content_id = (link_el["href"].rstrip("/").split("/")[-1]) if link_el else ""
            title_el = card.select_one(".ContentItem-title")
            author_el = card.select_one(".AuthorInfo-name")
            url = link_el["href"] if link_el else ""
            items.append(RawItem(
                external_id=str(content_id),
                source_platform="zhihu",
                url=url if url.startswith("http") else f"https://www.zhihu.com{url}",
                title=title_el.get_text(strip=True) if title_el else "",
                raw_text=card.get_text(" ", strip=True),
                structured={
                    "author": author_el.get_text(strip=True) if author_el else "",
                    "platform": "知乎",
                    "referral_company": query.get("company", ""),
                },
            ))
        return items
