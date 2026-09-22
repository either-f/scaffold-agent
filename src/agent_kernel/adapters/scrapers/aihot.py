"""AI 热点新闻聚合抓取。数据源：https://www.aibase.com/news（公开，无需登录/API key）。

ponytail: 该站点真实 CSS class 名未在本次会话核实（只通过 WebFetch 拿到渲染后的
markdown 结构描述，不是原始 HTML），解析逻辑按"每条新闻是一个包裹 <h3> 标题的
<a> 标签，紧跟一段摘要文本"这个通用形状写，宽松匹配（找所有含 h3 的 a 标签）而不是
死绑某个 class；真实联网跑一次 fetch() 校验字段是否对齐，不对齐只需改这一个文件。
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urljoin

from . import RawItem

HttpGet = Callable[[str], str]  # 返回响应体文本


class AiHotScraper:
    """构造函数可注入 http_get（默认用 requests.get(url, timeout=10).text），
    便于离线单测传假响应，不打真实网络。"""

    SOURCE_PLATFORM = "aihot"
    BASE_URL = "https://www.aibase.com/news"

    def __init__(self, http_get: HttpGet | None = None) -> None:
        self._http_get = http_get or self._default_http_get

    @staticmethod
    def _default_http_get(url: str) -> str:
        import requests

        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.text

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        limit = query.get("limit", 30)
        html = self._http_get(self.BASE_URL)
        return self._parse(html)[:limit]

    def _parse(self, html: str) -> list[RawItem]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        items: list[RawItem] = []
        for a in soup.find_all("a", href=True):
            h3 = a.find("h3")
            if h3 is None:
                continue
            title = h3.get_text(strip=True)
            if not title:
                continue
            summary_tag = h3.find_next_sibling(["p", "div", "span"])
            summary = summary_tag.get_text(strip=True) if summary_tag else ""
            url = urljoin(self.BASE_URL, a["href"])
            items.append(
                RawItem(
                    external_id=url,
                    source_platform=self.SOURCE_PLATFORM,
                    url=url,
                    title=title,
                    raw_text=f"{title}\n{summary}",
                    structured={"category": "ai", "summary": summary, "publish_time": None},
                )
            )
        return items
