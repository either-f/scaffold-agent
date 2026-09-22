"""小红书 内推文章搜索抓取。

# ponytail: 未验证真实反爬绕过（小红书搜索接口对签名参数/风控极敏感，公开 HTML 结构
# 也频繁变动）与登录态。选择器按公开搜索结果页结构编写，可运行但未经真实网络验证，
# 接入生产前需要用真实浏览器核对当前页面结构，大概率需要走官方/半官方 API 而非直接
# HTML 解析。
"""
from __future__ import annotations

from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str, dict[str, Any]], str]

SEARCH_URL = "https://www.xiaohongshu.com/search_result"


def _default_http_get(url: str, params: dict[str, Any]) -> str:
    import requests

    resp = requests.get(
        url, params=params, timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.text


class XhsScraper:
    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        html = self._http_get(SEARCH_URL, {"keyword": query.get("keyword", "内推")})
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for card in soup.select(".note-item"):
            link_el = card.select_one("a")
            note_id = (link_el["href"].rstrip("/").split("/")[-1]) if link_el else ""
            title_el = card.select_one(".title")
            author_el = card.select_one(".author")
            url = link_el["href"] if link_el else ""
            items.append(RawItem(
                external_id=str(note_id),
                source_platform="xhs",
                url=url if url.startswith("http") else f"https://www.xiaohongshu.com{url}",
                title=title_el.get_text(strip=True) if title_el else "",
                raw_text=card.get_text(" ", strip=True),
                structured={
                    "author": author_el.get_text(strip=True) if author_el else "",
                    "platform": "小红书",
                    "referral_company": query.get("company", ""),
                },
            ))
        return items
