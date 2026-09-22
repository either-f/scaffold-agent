"""GitHub 每日趋势榜单采集。

数据源用 GitHub 官方 trending 页（https://github.com/trending），不用 ghfind.com——
ghfind.com 本身也是二次聚合 GitHub trending 的站点，直接打官方源更稳定、字段更全，
ghfind.com 只是产品设想里的入口名字，不是必须依赖的数据源。

HTML 结构经本次会话真实联网验证（2026-08-12，见开发时抓的样本）：
`article.Box-row` 每条一个仓库，`h2 a[href]` 是 "/owner/repo"，`p` 是描述，
`[itemprop=programmingLanguage]` 是语言，`a.Link--muted[href$=stargazers]` 文本是总
star 数，`span.d-inline-block.float-sm-right` 文本形如 "1,616 stars today"。
GitHub 页面结构可能随时改版，属于外部依赖固有风险，不是本 adapter 能规避的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str], str]


def _default_http_get(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


@dataclass
class GhfindScraper:
    """`http_get` 注入点：测试传假实现，不打真实网络。"""

    http_get: HttpGet = _default_http_get
    base_url: str = "https://github.com/trending"

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        language = query.get("language")
        since = query.get("since", "daily")  # daily | weekly | monthly
        url = self.base_url
        if language:
            url = f"{url}/{language}"
        url = f"{url}?since={since}"

        html = self.http_get(url)
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for row in soup.select("article.Box-row"):
            link = row.select_one("h2 a[href]")
            if link is None:
                continue
            repo_path = link["href"].strip("/")  # "owner/repo"
            desc_tag = row.select_one("p")
            description = desc_tag.get_text(strip=True) if desc_tag else ""
            lang_tag = row.select_one("[itemprop=programmingLanguage]")
            lang = lang_tag.get_text(strip=True) if lang_tag else None
            star_tag = row.select_one("a.Link--muted[href$='/stargazers']")
            stars = _parse_int(star_tag.get_text(strip=True)) if star_tag else 0
            today_tag = row.select_one("span.d-inline-block.float-sm-right")
            today_stars = _parse_int(today_tag.get_text(strip=True)) if today_tag else 0

            items.append(
                RawItem(
                    external_id=repo_path,
                    source_platform="ghfind",
                    url=f"https://github.com/{repo_path}",
                    title=repo_path,
                    raw_text=description,
                    structured={
                        "repo_url": f"https://github.com/{repo_path}",
                        "description": description,
                        "language": lang,
                        "stars": stars,
                        "today_stars": today_stars,
                    },
                    score=float(today_stars),
                )
            )
        return items


def _parse_int(text: str) -> int:
    """'1,616 stars today' / '7,499' -> int，找不到数字返回 0。"""
    match = re.search(r"[\d,]+", text)
    if not match:
        return 0
    return int(match.group(0).replace(",", ""))
