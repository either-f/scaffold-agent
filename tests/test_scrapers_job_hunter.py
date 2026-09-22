"""招聘/内推模块采集层离线测试：假 http_get 验证各 scraper 解析正确，
以及 SqliteContentStore.upsert 去重。不打真实网络。

运行：PYTHONPATH=src python3 tests/test_scrapers_job_hunter.py   （也兼容 pytest）
"""
import sys

sys.path.insert(0, "src")

from agent_kernel.adapters.content_store import SqliteContentStore
from agent_kernel.adapters.scrapers.boss import BossScraper
from agent_kernel.adapters.scrapers.douyin import DouyinScraper
from agent_kernel.adapters.scrapers.xhs import XhsScraper
from agent_kernel.adapters.scrapers.zhihu import ZhihuScraper
from agent_kernel.adapters.scrapers.zhilian import ZhilianScraper

BOSS_HTML = """
<div class="job-card-wrapper" data-jobid="j1">
  <a href="/job_detail/j1.html">
    <span class="job-name">Python工程师</span>
  </a>
  <span class="company-name">某厂</span>
  <span class="salary">20-30K</span>
  <span class="job-area">上海</span>
  <ul class="tag-list"><li>1-3年</li><li>本科</li></ul>
</div>
"""

ZHILIAN_HTML = """
<div class="joblist-box__item">
  <a href="/job_detail/z1"></a>
  <span class="jobinfo__name">后端开发</span>
  <span class="companyinfo__name">另一厂</span>
  <span class="jobinfo__salary">15-25K</span>
  <span class="jobinfo__city">北京</span>
  <div class="jobinfo__tags"><span>3-5年</span></div>
</div>
"""

XHS_HTML = """
<div class="note-item">
  <a href="/explore/xhs1"><span class="title">字节内推来啦</span></a>
  <span class="author">某同学</span>
</div>
"""

ZHIHU_HTML = """
<div class="SearchResult-Card">
  <a href="/question/zh1">
    <span class="ContentItem-title">腾讯内推攻略</span>
  </a>
  <span class="AuthorInfo-name">知乎用户</span>
</div>
"""

DOUYIN_HTML = """
<div class="search-item">
  <a href="/video/dy1"><span class="title">快手内推信息</span></a>
  <span class="author-name">抖音用户</span>
</div>
"""


def _fake_http_get(html: str):
    return lambda url, params: html


def test_boss_scraper_parses_items():
    scraper = BossScraper(http_get=_fake_http_get(BOSS_HTML))
    items = scraper.fetch({"keyword": "python", "city": "101010100"})
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "j1"
    assert item.source_platform == "boss"
    assert item.title == "Python工程师"
    assert item.structured["company"] == "某厂"
    assert item.structured["salary"] == "20-30K"
    assert item.structured["city"] == "上海"
    assert item.structured["tags"] == ["1-3年", "本科"]
    assert item.url == "https://www.zhipin.com/job_detail/j1.html"


def test_zhilian_scraper_parses_items():
    scraper = ZhilianScraper(http_get=_fake_http_get(ZHILIAN_HTML))
    items = scraper.fetch({"keyword": "java"})
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "z1"
    assert item.source_platform == "zhilian"
    assert item.title == "后端开发"
    assert item.structured["company"] == "另一厂"
    assert item.structured["salary"] == "15-25K"
    assert item.structured["city"] == "北京"


def test_xhs_scraper_parses_items():
    scraper = XhsScraper(http_get=_fake_http_get(XHS_HTML))
    items = scraper.fetch({"keyword": "字节内推", "company": "字节跳动"})
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "xhs1"
    assert item.source_platform == "xhs"
    assert item.title == "字节内推来啦"
    assert item.structured["author"] == "某同学"
    assert item.structured["platform"] == "小红书"
    assert item.structured["referral_company"] == "字节跳动"


def test_zhihu_scraper_parses_items():
    scraper = ZhihuScraper(http_get=_fake_http_get(ZHIHU_HTML))
    items = scraper.fetch({"keyword": "腾讯内推"})
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "zh1"
    assert item.source_platform == "zhihu"
    assert item.title == "腾讯内推攻略"
    assert item.structured["author"] == "知乎用户"


def test_douyin_scraper_parses_items():
    scraper = DouyinScraper(http_get=_fake_http_get(DOUYIN_HTML))
    items = scraper.fetch({"keyword": "快手内推"})
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "dy1"
    assert item.source_platform == "douyin"
    assert item.title == "快手内推信息"
    assert item.structured["author"] == "抖音用户"


def test_content_store_dedup_on_external_id():
    store = SqliteContentStore(":memory:")
    scraper = BossScraper(http_get=_fake_http_get(BOSS_HTML))
    item = scraper.fetch({"keyword": "python"})[0]

    first_id = store.upsert("job-hunter", item)
    second_id = store.upsert("job-hunter", item)

    assert first_id is not None
    assert second_id is None
    assert len(store.list_items(module="job-hunter")) == 1


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok: {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    run_all()
