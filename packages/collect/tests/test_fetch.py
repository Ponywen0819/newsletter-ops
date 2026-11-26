import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from newsletter_collect.fetch import (
    Item, clean_html, http_get, fetch_all, page_getter, parse_date, parse_feed, retryable, run_by_host, source_host,
    stale_warning, wait_seconds,
)

CFG = {"delay_seconds": 1.0, "domain_delay_seconds": {"export.arxiv.org": 3.0}}


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "x", {}, None)  # type: ignore[arg-type]


def test_source_host():
    assert source_host({"type": "arxiv", "query": "cat:cs.LG"}) == "export.arxiv.org"
    assert source_host({"type": "rss", "url": "https://Feeds.BBCI.co.uk/news/rss.xml"}) == "feeds.bbci.co.uk"


@pytest.mark.parametrize(("host", "last", "want"), [
    ("a.com", {}, 0.0),                                        # 第一次請求不等
    ("a.com", {"b.com": 99.9}, 0.0),                           # 別的網域不等
    ("a.com", {"a.com": 99.5}, 0.5),                           # 同網域補足 1 秒
    ("a.com", {"a.com": 90.0}, 0.0),                           # 早就超過間隔
    ("export.arxiv.org", {"export.arxiv.org": 99.0}, 2.0),     # arXiv 3 秒
])
def test_wait_seconds(host, last, want):
    assert wait_seconds(host, last, CFG, 100.0) == want


def test_retryable():
    assert not retryable(http_error(404)) and not retryable(http_error(403))
    assert retryable(http_error(429)) and retryable(http_error(503)) and retryable(TimeoutError())


def test_404_not_retried(monkeypatch):
    calls = []

    def urlopen(*a, **k):
        calls.append(1)
        raise http_error(404)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", calls.append)
    with pytest.raises(RuntimeError, match="404"):
        http_get("https://x/feed", {"retries": 2})
    assert calls == [1]  # 只請求一次、不等


def dated(day: str) -> Item:
    return Item(uid=day, title="t", url="u", source="s", topic="t", weight=1.0, published=f"{day}T00:00:00+00:00",
                summary="", authors=[])


def test_stale_warning():
    ref = datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert stale_warning({"name": "A"}, [dated("2026-10-01"), dated("2026-10-10")], ref) == \
        "A: 最新一則是 29 天前（2026-10-10），feed 可能停更"
    assert stale_warning({"name": "A"}, [dated("2026-10-20")], ref) is None
    assert stale_warning({"name": "A", "stale_days": 7}, [dated("2026-10-20")], ref) is not None
    assert stale_warning({"name": "A"}, [], ref) is None


def test_fetch_all_parallel():
    """同網域依序且守間隔、不同網域同時；結果照設定順序。"""
    srcs = [{"name": n, "type": "rss", "url": f"https://{h}/{n}"} for n, h in
            (("a1", "a.x"), ("b1", "b.x"), ("a2", "a.x"), ("c1", "c.x"))]
    hits: list[tuple[str, float]] = []

    def fake(src: dict, _cfg: dict, _limit: int):
        hits.append((src["name"], time.monotonic()))
        time.sleep(0.2)
        return [Item(uid=src["name"], title=src["name"], url=src["url"], source=src["name"], topic="t",
                     weight=1.0, published=None, summary="", authors=[])], \
            (f"{src['name']}: boom" if src["name"] == "b1" else None)

    t0 = time.monotonic()
    got, errs = fetch_all({"sources": srcs, "request": {"delay_seconds": 0.3}}, fake)
    assert [i.uid for i in got] == ["a1", "b1", "a2", "c1"] and errs == ["b1: boom"]
    at = dict(hits)
    assert at["a2"] - at["a1"] >= 0.2 + 0.3 - 0.01  # a.x 第二個要等第一個做完再隔 0.3 秒
    assert abs(at["b1"] - at["a1"]) < 0.15 and abs(at["c1"] - at["a1"]) < 0.15  # 不同網域同時開始
    assert time.monotonic() - t0 < 1.0


def test_skip_pattern():
    rss = (b"<rss><channel>"
           b"<item><title>Real news</title><link>https://x/news/1</link></item>"
           b"<item><title>Watch: a ship sinks</title><link>https://x/news/2</link></item>"
           b"<item><title>Red carpet</title><link>https://x/news/videos/3</link></item>"
           b"</channel></rss>")
    assert [i.title for i in parse_feed(rss, {"name": "s", "skip": r"^Watch:|/videos/"}, 10)] == ["Real news"]
    assert len(parse_feed(rss, {"name": "s"}, 10)) == 3


def test_page_getter(monkeypatch):
    """抓網頁：不重試、逾時 15 秒（可改）、壞掉的 UTF-8 不丟例外。"""
    from newsletter_collect import fetch

    seen = {}

    def fake(url, cfg):
        seen.update(cfg, url=url)
        return b"ok \xff"
    monkeypatch.setattr(fetch, "http_get", fake)
    get = page_getter({"request": {"retries": 3, "timeout": 25, "user_agent": "ua"}})
    assert get("https://a.example/x") == "ok \ufffd"
    assert seen == {"retries": 0, "timeout": 15, "user_agent": "ua", "url": "https://a.example/x"}
    page_getter({}, timeout=5)("https://b")
    assert seen["timeout"] == 5


def test_run_by_host():
    """沒有工作不出錯；同一網域依序、不同網域各自做。"""
    run_by_host([], lambda j: "h", lambda j: None, {})
    done: list[str] = []
    run_by_host(["a1", "b1", "a2"], lambda j: j[0], done.append, {"delay_seconds": 0})
    assert sorted(done) == ["a1", "a2", "b1"] and done.index("a1") < done.index("a2")


def test_parse_date_and_clean_html():
    assert parse_date("Mon, 05 Oct 2026 08:00:00 GMT") == "2026-10-05T08:00:00+00:00"
    assert parse_date("2026-10-05T08:00:00Z") == "2026-10-05T08:00:00+00:00"
    assert parse_date("2026-10-05T08:00:00") == "2026-10-05T08:00:00+00:00"  # 沒有時區當 UTC
    assert parse_date("不是日期") is None and parse_date(None) is None
    assert clean_html("<p>a&amp;b <script>x()</script> c</p>") == "a&b c"
    assert clean_html("x" * 50, limit=10) == "x" * 10
