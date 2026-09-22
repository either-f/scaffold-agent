"""智联招聘 岗位搜索抓取。

# ponytail: 未验证真实反爬绕过与登录态，同 boss.py 的坦诚声明——选择器按公开搜索页
# 结构编写，接入生产前需用真实浏览器核对当前 DOM 结构。
"""
from __future__ import annotations

from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str, dict[str, Any]], str]

SEARCH_URL = "https://sou.zhaopin.com/"


def _default_http_get(url: str, params: dict[str, Any]) -> str:
    import requests

    resp = requests.get(
        url, params=params, timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.text


class ZhilianScraper:
    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        html = self._http_get(SEARCH_URL, {
            "kw": query.get("keyword", ""),
            "city": query.get("city", "530"),
        })
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for card in soup.select(".joblist-box__item"):
            link_el = card.select_one("a")
            job_id = (link_el["href"].rstrip("/").split("/")[-1]) if link_el else ""
            title_el = card.select_one(".jobinfo__name")
            company_el = card.select_one(".companyinfo__name")
            salary_el = card.select_one(".jobinfo__salary")
            city_el = card.select_one(".jobinfo__city")
            tags = [t.get_text(strip=True) for t in card.select(".jobinfo__tags span")]
            url = link_el["href"] if link_el else ""
            items.append(RawItem(
                external_id=str(job_id),
                source_platform="zhilian",
                url=url if url.startswith("http") else f"https://sou.zhaopin.com{url}",
                title=title_el.get_text(strip=True) if title_el else "",
                raw_text=card.get_text(" ", strip=True),
                structured={
                    "company": company_el.get_text(strip=True) if company_el else "",
                    "salary": salary_el.get_text(strip=True) if salary_el else "",
                    "city": city_el.get_text(strip=True) if city_el else "",
                    "tags": tags,
                },
            ))
        return items
