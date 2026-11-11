from datetime import datetime, timedelta, timezone

import pytest

from newsletter_collect import fetch
from newsletter_collect.search import accept, gaps, parse, prompt, public_host, title_matches

NOW = datetime.now(timezone.utc)
CONFIG = {"topics": {"world": "國際新聞", "science": "科學", "economy": "商業"},
          "search": {"always": ["world"], "per_gap": 1, "lookback_hours": 48}}
RANKED = [{"topic": "world", "title": "Old headline", "url": "https://bbc.example/a?utm_source=x"},
          {"topic": "economy", "title": "Rates", "url": "https://e.example/r"}]
PAGE = '<html><head><title>Ceasefire agreed in talks | Reuters</title></head></html>'


def test_gaps_and_prompt():
    assert gaps(RANKED, CONFIG) == ["world", "science"]  # world 固定補；science 0 則；economy 有東西
    p = prompt(["world", "science"], RANKED, CONFIG, NOW)
    assert "world（國際新聞）：目前候選 1 則" in p and "  - Old headline" in p and "science（科學）：目前候選 0 則" in p


def test_parse():
    assert parse('好的\n{"items": [{"a": 1}]}\n') == [{"a": 1}]


@pytest.mark.parametrize("bad", ["沒有", '{"x": 1}', '{"items": {}}'])
def test_parse_bad(bad):
    with pytest.raises(ValueError):
        parse(bad)


@pytest.mark.parametrize("host", ["localhost", "web", "worker", "127.0.0.1", "10.0.0.5", "192.168.1.1",
                                  "169.254.169.254", "::1", "fd00::1", "a.localhost", ""])
def test_private_hosts(host):
    assert not public_host(host)


@pytest.mark.parametrize("host", ["bbc.co.uk", "WWW.Reuters.com", "8.8.8.8", "2001:4860:4860::8888"])
def test_public_hosts(host):
    assert public_host(host)


def test_title_matches():
    assert title_matches("Ceasefire agreed in talks", PAGE)
    assert title_matches("Ceasefire agreed in talks", '<meta property="og:title" content="Ceasefire agreed in talks &amp; more">')
    assert not title_matches("Something else entirely", PAGE)


def test_accept():
    pages = {"https://r.example/ok": PAGE, "https://r.example/ok2": PAGE, "https://r.example/seen": PAGE}

    def get(url: str) -> str:
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    def item(**kw) -> dict:
        return {"topic": "world", "title": "Ceasefire agreed in talks", "url": "https://r.example/ok",
                "published": (NOW - timedelta(hours=3)).isoformat(), "summary": "s", "query": "q", **kw}

    found = [item(), item(url="https://r.example/ok2"), item(topic="economy"), item(url="ftp://x"),
             item(published="昨天"), item(published=(NOW - timedelta(hours=49)).isoformat()), item(topic="science", url="https://r.example/404"),
             item(topic="science", title="Wrong title here", url="https://r.example/ok2"), item(url="https://bbc.example/a"),
             {"topic": "world"}, "not a dict",
             item(topic="science", url="https://r.example/seen"), item(url="http://worker:8790/status")]
    seen = {fetch.make_uid("https://r.example/seen", "Ceasefire agreed in talks"): "x"}
    kept, rejected = accept(found, RANKED, seen, CONFIG, get)
    assert [i["url"] for i in kept] == ["https://r.example/ok"]
    k = kept[0]
    assert k["origin"] == "search" and k["source"] == "搜尋：r.example" and k["query"] == "q" and len(k["uid"]) == 16
    assert k["published"] == fetch._parse_date((NOW - timedelta(hours=3)).isoformat())
    assert [r["reason"] for r in rejected] == [
        "超過每個主題 1 則", "主題不在這次要補的清單", "網址不是 http(s)", "發佈時間無法解析",
        "發佈時間不在 48 小時內", "網址抓不到：404", "頁面標題對不上", "和已收錄的項目重複",
        "欄位不齊", "欄位不齊", "前幾天已收錄過", "網址不是公開網站"]
