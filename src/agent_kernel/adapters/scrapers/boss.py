"""BOSS直聘 岗位搜索抓取。

# ponytail: 未验证真实反爬绕过（BOSS直聘对非浏览器 UA/高频请求有滑块验证与封禁策略）、
# 未验证真实登录态（内推/主动沟通类接口通常要求登录 cookie）。HTTP 请求结构、参数与
# HTML 解析选择器按公开搜索页结构编写，可运行但未经真实网络验证；接入生产前需要用
# 真实浏览器抓一份当前页面结构核对选择器，并评估是否需要 Playwright + 人工登录态复用
# 才能稳定过反爬（见项目设计里"招聘平台采集单独评估"的结论）。
"""
from __future__ import annotations

from typing import Any, Callable

from bs4 import BeautifulSoup

from . import RawItem

HttpGet = Callable[[str, dict[str, Any]], str]

SEARCH_URL = "https://www.zhipin.com/web/geek/job"


def _default_http_get(url: str, params: dict[str, Any]) -> str:
    import requests

    resp = requests.get(
        url, params=params, timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.text


class BossScraper:
    """`http_get` 可注入假实现做离线单测，默认真实发 HTTP 请求。"""

    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        html = self._http_get(SEARCH_URL, {
            "query": query.get("keyword", ""),
            "city": query.get("city", "101010100"),
        })
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for card in soup.select(".job-card-wrapper"):
            job_id = card.get("data-jobid") or card.select_one("a")["href"].split("/")[-1]
            title_el = card.select_one(".job-name")
            company_el = card.select_one(".company-name")
            salary_el = card.select_one(".salary")
            city_el = card.select_one(".job-area")
            tags = [t.get_text(strip=True) for t in card.select(".tag-list li")]
            link = card.select_one("a")["href"]
            url = link if link.startswith("http") else f"https://www.zhipin.com{link}"
            items.append(RawItem(
                external_id=str(job_id),
                source_platform="boss",
                url=url,
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
