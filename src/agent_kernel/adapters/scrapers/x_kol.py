"""追踪指定关键人物在 X（Twitter）上的动态。X 官方 API v2 需要付费 Bearer Token，
本 adapter 不假装绕过这个限制——没有 token 时 fetch() 直接抛清晰错误，不返回假数据。

ponytail: 只实现"按用户名拉最近推文"这一个端点（GET /2/users/by/username/{u} 换 id，
再 GET /2/users/{id}/tweets），不做流式/webhook 订阅——批量日报场景轮询已经够用。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from . import RawItem

HttpGet = Callable[[str, dict[str, str]], dict]  # (url, headers) -> 解析后的 JSON dict


class XKolScraper:
    """accounts：要追踪的用户名列表（不带 @），构造时传入，不硬编码死。
    bearer_token 不传则读环境变量 X_BEARER_TOKEN；都没有，fetch() 时报错。
    http_get 可注入假实现做离线单测。"""

    SOURCE_PLATFORM = "x"
    API_BASE = "https://api.twitter.com/2"

    def __init__(
        self,
        accounts: list[str],
        bearer_token: str | None = None,
        http_get: HttpGet | None = None,
    ) -> None:
        self.accounts = accounts
        self._bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN")
        self._http_get = http_get or self._default_http_get

    @staticmethod
    def _default_http_get(url: str, headers: dict[str, str]) -> dict:
        import requests

        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def fetch(self, query: dict[str, Any]) -> list[RawItem]:
        if not self._bearer_token:
            raise RuntimeError(
                "缺少 X_BEARER_TOKEN：X API v2 需要付费开发者账号的 Bearer Token，"
                "设置环境变量或传 bearer_token 构造参数后才能调用"
            )
        headers = {"Authorization": f"Bearer {self._bearer_token}"}
        model_keywords = query.get(
            "model_keywords", ["model", "release", "credit", "reset", "gpt", "claude", "gemini"]
        )
        max_results = query.get("max_results", 10)

        items: list[RawItem] = []
        for username in self.accounts:
            user = self._http_get(f"{self.API_BASE}/users/by/username/{username}", headers)
            user_id = user.get("data", {}).get("id")
            if not user_id:
                continue
            tweets = self._http_get(
                f"{self.API_BASE}/users/{user_id}/tweets?max_results={max_results}"
                "&tweet.fields=created_at",
                headers,
            )
            for tweet in tweets.get("data", []):
                text = tweet.get("text", "")
                is_model_update = any(kw.lower() in text.lower() for kw in model_keywords)
                items.append(
                    RawItem(
                        external_id=tweet["id"],
                        source_platform=self.SOURCE_PLATFORM,
                        url=f"https://x.com/{username}/status/{tweet['id']}",
                        title=text[:60],
                        raw_text=text,
                        structured={
                            "author": username,
                            "posted_at": tweet.get("created_at"),
                            "is_model_update": is_model_update,
                        },
                    )
                )
        return items
