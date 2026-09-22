"""平台采集器：脚本抓取公开网页，写入 PlatformStore。

三个采集器全部是爬虫脚本——直接抓取公开 HTML/JSON 页面，不依赖任何第三方 API
配额（上一版 GitHub Search 匿名 60 次/小时的限制不再是约束；招聘也从本地模拟
换成真实抓取）：

- `github-trending` -> https://github.com/trending/python?since=daily（真实榜单）
- `ai-daily-news`   -> https://www.v2ex.com/api/topics/hot.json（V2EX 热门话题 +
  关键词过滤，零配置公开 JSON，见 agent-reach skill 的 social.md）
- `job-hunter`      -> https://www.v2ex.com/go/jobs（V2EX 酷工作节点，公开无登录）

ponytail: 原计划 ai-daily-news 接 Hacker News，实测 news.ycombinator.com 在本机
网络环境连接被重置（ConnectionResetError），换成同样零配置、但连通的 V2EX 热门
话题接口。Reddit 也考虑过（用户要求），但 agent-reach doctor 显示 Reddit 没有
零配置路径——匿名 .json 已被封，必须 OpenCLI/rdt-cli 登录态，服务器无头环境
装不了、也不该把个人登录 cookie 塞进这个无人值守的采集脚本，先不接，要接的话
装 rdt-cli 走人工登录后再补一个 collect_reddit_ai 函数即可，接口形状不变。

依赖走 `scrapers` extra（requests + beautifulsoup4），只在 adapter 层引用，符合
「内核零依赖、adapter 才引依赖」的纪律。旧入口 best-effort 返回新增数；strict=True
抛安全错误。运行入口区分失败、部分保存与有效空结果；HTML 无预期结构且无明确
空状态时记解析失败。`parse_*` 解析函数是纯函数（输入 HTML 字符串、输出
结构化 dict），离线喂 fixture 即可单测（见 tests/test_platform_collectors.py），
符合「每个真 adapter 必须有 Fake 对照」的扩展纪律。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .platform_store import PlatformStore

GITHUB_TRENDING_URL = "https://github.com/trending/python?since=daily"
V2EX_HOT_JSON_URL = "https://www.v2ex.com/api/topics/hot.json"
V2EX_JOBS_URL = "https://www.v2ex.com/go/jobs"
V2EX_BASE_URL = "https://www.v2ex.com"

TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

AI_KEYWORDS = (
    "ai", "llm", "gpt", "agent", "大模型", "智能体", "openai", "anthropic",
    "gemini", "claude", "deepseek", "模型",
)
CITY_PATTERN = re.compile(r"[\[【]([^\]】]{1,8})[\]】]")
# Conservative, explicit place labels only; unlisted/ambiguous labels stay unknown.
KNOWN_CITIES = frozenset("北京 上海 广州 深圳 杭州 南京 苏州 无锡 宁波 成都 重庆 武汉 西安 "
                        "天津 厦门 福州 合肥 长沙 郑州 济南 青岛 大连 沈阳 长春 哈尔滨 "
                        "石家庄 太原 南昌 南宁 昆明 贵阳 海口 兰州 西宁 银川 呼和浩特 "
                        "乌鲁木齐 拉萨 东莞 佛山 珠海 惠州 香港 澳门 台北 新加坡".split())


class CollectionError(RuntimeError):
    """Public collection failures contain only fixed reasons or exception class names."""


class CollectionParseError(ValueError):
    """The response cannot be identified as the expected content or an explicit empty state."""


def _fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _fetch_json(url: str) -> list[dict[str, Any]]:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _canonical_url(url: str) -> str:
    url = urldefrag((url or "").strip())[0]
    parts = urlsplit(url)
    topic = re.fullmatch(r"/t/(\d+)/?", parts.path)
    if topic and (not parts.netloc or parts.hostname in {"v2ex.com", "www.v2ex.com"}):
        return f"{V2EX_BASE_URL}/t/{topic.group(1)}"
    return url


def _safe_error(exc: Exception) -> str:
    return type(exc).__name__


def _record(stats: dict[str, int] | None, result: str) -> None:
    if stats is not None:
        stats[result] = stats.get(result, 0) + 1


def _parse_stars(text: str) -> int:
    """'1,234' / '12.3k' / '1.2m' -> int"""
    text = text.strip().replace(",", "").lower()
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("m"):
            return int(float(text[:-1]) * 1_000_000)
        return int(text)
    except ValueError:
        return 0


# ---- 解析（纯函数，可离线 fixture 单测） ------------------------------------
def parse_github_trending(html: str, *, strict: bool = False) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for row in soup.select("article.Box-row"):
        h2 = row.select_one("h2 a")
        if h2 is None:
            continue
        full_name = (h2.get("href") or "").strip("/")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", full_name):
            continue
        desc_el = row.select_one("p")
        desc = desc_el.get_text(strip=True) if desc_el else ""
        stars_el = row.select_one("a[href$='/stargazers']")
        stars = _parse_stars(stars_el.get_text(strip=True)) if stars_el else 0
        lang_el = row.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else "Unknown"
        out.append({
            "name": full_name.split("/")[-1],
            "full_name": full_name,
            "stars": stars,
            "language": language,
            "description": desc[:200],
        })
    if strict and not out:
        empty = soup.select_one(".blankslate")
        if empty is None or not re.search(r"no (?:trending )?repositories", empty.get_text(" "), re.I):
            raise CollectionParseError("unexpected GitHub HTML structure")
    return out


def parse_v2ex_hot(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """V2EX 热门话题 JSON -> 按 AI_KEYWORDS 过滤标题+正文，最多取 5 条。"""
    out: list[dict[str, Any]] = []
    if not isinstance(topics, list):
        raise CollectionParseError("unexpected V2EX JSON structure")
    for t in topics:
        if not isinstance(t, dict) or not isinstance(t.get("title"), str):
            raise CollectionParseError("unexpected V2EX topic structure")
        title = (t.get("title") or "").strip()
        content = (t.get("content") or "").strip()
        haystack = f"{title} {content}".lower()
        if not title or not any(k in haystack for k in AI_KEYWORDS):
            continue
        url = _canonical_url(t.get("url") or "")
        external_id = t.get("id")
        if external_id is None:
            path_parts = urlsplit(url).path.rstrip("/").split("/")
            if len(path_parts) >= 3 and path_parts[-2] == "t":
                external_id = path_parts[-1]
        if external_id is not None and not url:
            url = f"{V2EX_BASE_URL}/t/{external_id}"
        published_at = None
        if t.get("created") is not None:
            try:
                published_at = datetime.fromtimestamp(float(t["created"]), timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                raise CollectionParseError("invalid V2EX publication timestamp") from None
        out.append({
            "title": title[:200], "url": url,
            "external_id": str(external_id) if external_id is not None else None,
            "summary": content[:200], "published_at": published_at,
        })
        if len(out) >= 5:
            break
    return out


def parse_v2ex_jobs(html: str, *, strict: bool = False) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for a in soup.select("span.item_title > a"):
        title = a.get_text(strip=True)
        if not title:
            continue
        labels = [label.strip() for label in CITY_PATTERN.findall(title)]
        locations = {label for label in labels if label in KNOWN_CITIES or label == "远程"}
        # Bare labels are accepted only at the start and with a clear separator.
        leading = re.match(r"^([^\s|｜:：]{1,8})(?:\s|[|｜:：])", title)
        if leading and (leading.group(1) in KNOWN_CITIES or leading.group(1) == "远程"):
            locations.add(leading.group(1))
        city = next(iter(locations)) if len(locations) == 1 else ""
        href = _canonical_url(urljoin(V2EX_BASE_URL, a.get("href") or ""))
        path_parts = urlsplit(href).path.rstrip("/").split("/")
        external_id = path_parts[-1] if len(path_parts) >= 3 and path_parts[-2] == "t" else None
        if not external_id or not external_id.isdigit():
            continue
        out.append({
            "title": title[:100], "city": city, "external_id": external_id,
            "url": href,
        })
        if len(out) >= 8:
            break
    if strict and not out:
        node = soup.select_one("#Main .box")
        node_text = node.get_text(" ", strip=True) if node else ""
        if "酷工作" not in node_text or not any(
            marker in node_text for marker in ("暂无主题", "还没有主题", "暂无话题")
        ):
            raise CollectionParseError("unexpected V2EX jobs HTML structure")
    return out


# ---- 采集入口 --------------------------------------------------------------
def collect_github_trending(
    store: PlatformStore, *, strict: bool = False, _stats: dict[str, int] | None = None,
) -> int:
    try:
        html = _fetch_html(GITHUB_TRENDING_URL)
        items = parse_github_trending(html, strict=True)
    except Exception as exc:  # noqa: BLE001 - 采集 best-effort
        print(f"[collect] github trending failed: {_safe_error(exc)}")
        if strict:
            raise CollectionError(_safe_error(exc)) from None
        return 0
    count = 0
    for item in items:
        try:
            result = store._upsert_project_result(
                name=item["name"],
                full_name=item["full_name"],
                stars=item["stars"],
                language=item["language"],
                description=item["description"],
                tags=["Trending"],
                tech="",
                external_id=item["full_name"],
                source_platform="github",
                source_url=f"https://github.com/{item['full_name']}",
                origin="real",
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not erase the run
            if _stats is None and strict:
                raise CollectionError(_safe_error(exc)) from None
            print(f"[collect] github trending item failed: {_safe_error(exc)}")
            if _stats is not None:
                _stats["errors"] = _stats.get("errors", 0) + 1
            continue
        _record(_stats, result)
        if result == "inserted":
            count += 1
    print(f"[collect] github trending: {count} new project(s)")
    return count


def collect_ai_news(
    store: PlatformStore, *, strict: bool = False, _stats: dict[str, int] | None = None,
) -> int:
    try:
        topics = _fetch_json(V2EX_HOT_JSON_URL)
        items = parse_v2ex_hot(topics)
    except Exception as exc:  # noqa: BLE001
        print(f"[collect] v2ex hot topics failed: {_safe_error(exc)}")
        if strict:
            raise CollectionError(_safe_error(exc)) from None
        return 0
    count = 0
    for item in items:
        try:
            result = store._upsert_news_result(
                title=item["title"],
                source="V2EX 热门",
                summary=item["summary"],
                external_id=item["external_id"],
                source_platform="v2ex-hot",
                source_url=item["url"],
                origin="real",
                published_at=item["published_at"],
            )
        except Exception as exc:  # noqa: BLE001
            if _stats is None and strict:
                raise CollectionError(_safe_error(exc)) from None
            print(f"[collect] v2ex hot item failed: {_safe_error(exc)}")
            if _stats is not None:
                _stats["errors"] = _stats.get("errors", 0) + 1
            continue
        _record(_stats, result)
        if result == "inserted":
            count += 1
    print(f"[collect] v2ex hot topics: {count} new item(s)")
    return count


def collect_jobs(
    store: PlatformStore, *, strict: bool = False, _stats: dict[str, int] | None = None,
) -> int:
    try:
        html = _fetch_html(V2EX_JOBS_URL)
        items = parse_v2ex_jobs(html, strict=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[collect] v2ex jobs failed: {_safe_error(exc)}")
        if strict:
            raise CollectionError(_safe_error(exc)) from None
        return 0
    count = 0
    for item in items:
        try:
            result = store._upsert_job_result(
                title=item["title"],
                city=item["city"],
                meta="",
                match=0,
                tags=["招聘", "V2EX"],
                reason="",
                external_id=item["external_id"],
                source_platform="v2ex-jobs",
                source_url=item["url"],
                origin="real",
                company="", direction="", grad_year="", salary="",
            )
        except Exception as exc:  # noqa: BLE001
            if _stats is None and strict:
                raise CollectionError(_safe_error(exc)) from None
            print(f"[collect] v2ex jobs item failed: {_safe_error(exc)}")
            if _stats is not None:
                _stats["errors"] = _stats.get("errors", 0) + 1
            continue
        _record(_stats, result)
        if result == "inserted":
            count += 1
    print(f"[collect] v2ex jobs: {count} new job(s)")
    return count


COLLECTORS = {
    "github-trending": collect_github_trending,
    "ai-daily-news": collect_ai_news,
    "job-hunter": collect_jobs,
}


def collect_for_module(
    store: PlatformStore,
    module_id: str,
    *,
    strict: bool = False,
    _stats: dict[str, int] | None = None,
) -> int:
    """scheduler callback 与手动触发共用的入口：按模块 id 分派采集器。"""
    collector = COLLECTORS.get(module_id)
    if collector is None:
        print("[collect] unknown module")
        if strict:
            raise CollectionError("unknown module")
        return 0
    if _stats is None:
        return collector(store, strict=strict)
    return collector(store, strict=strict, _stats=_stats)


def execute_collection_run(store: PlatformStore, module_id: str, run_id: str) -> None:
    """在独立连接上执行一次已入队采集；非 queued 运行不会重复执行。"""
    worker_store = store.fork()
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    try:
        run = worker_store.get_collection_run(run_id)
        if run is None or run["module_id"] != module_id:
            return
        if not worker_store.update_collection_run(run_id, status="running"):
            return
        status, error = "succeeded", ""
        try:
            collect_for_module(worker_store, module_id, strict=True, _stats=stats)
            if stats["errors"]:
                completed = stats["inserted"] + stats["updated"] + stats["skipped"]
                status = "partial" if completed else "failed"
                error = f"条目保存失败: {stats['errors']}"
        except Exception as exc:  # noqa: BLE001 - never expose exception text or credentials
            status, error = "failed", f"采集失败: {_safe_error(exc)}"
        worker_store.update_collection_run(
            run_id,
            status=status,
            inserted=stats["inserted"],
            updated=stats["updated"],
            skipped=stats["skipped"],
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 - DB persistence failure must not silently succeed
        raise CollectionError(_safe_error(exc)) from None
    finally:
        worker_store.close()
