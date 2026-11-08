"""補原文：最可能被選為重點的項目由程式抓原文，存進 curated 的 fulltext，writer 讀檔判斷，不必自己上網。

挑選：每個主題 rank 前 PER_TOPIC 則（arXiv 除外）、搜尋補來的全部、還沒有主會議資訊的 arXiv 前 PAPERS 篇
（讀 arxiv.org/html/<id>，開頭有作者機構；沒有 HTML 版就讀 abs 頁）。抓不到就記 fulltext_error，不擋流程。
"""
from __future__ import annotations

import html
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from newsletter_collect import fetch
from newsletter_shared import metrics

PER_TOPIC = 3
PAPERS = 3
CHARS = 3000
MAIN = re.compile(r"<(article|main)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)
DROP = re.compile(r"<(nav|header|footer|aside|form)\b.*?</\1\s*>", re.S | re.I)


def is_paper(item: dict) -> bool:
    return item.get("source", "").startswith("arXiv")


def targets(items: list[dict]) -> list[dict]:
    picked: list[dict] = []
    per_topic: Counter = Counter()
    papers = 0
    for item in sorted(items, key=lambda i: i.get("rank", 0), reverse=True):
        if is_paper(item):
            if (item.get("venue") or {}).get("status") != "main" and papers < PAPERS:
                picked.append(item)
                papers += 1
        elif item.get("origin") == "search" or per_topic[item["topic"]] < PER_TOPIC:
            picked.append(item)
            per_topic[item["topic"]] += item.get("origin") != "search"
    return picked


def urls_for(item: dict) -> list[str]:
    url = item["url"]
    if is_paper(item) and "/abs/" in url:
        return [url.replace("/abs/", "/html/"), url]
    return [url]


def text_of(page: str) -> str:
    """有 <article> 或 <main> 就只取它，否則取 <body>；去掉選單、頁首頁尾這類區塊。"""
    m = MAIN.search(page)
    body = m.group(2) if m else re.split(r"<body\b[^>]*>", page, maxsplit=1, flags=re.I)[-1]
    return html.unescape(fetch._clean(DROP.sub(" ", body), CHARS * 2))[:CHARS]


def enrich(items: list[dict], config: dict, get: Callable[[str], str] | None = None) -> None:
    """就地加上 fulltext 或 fulltext_error。"""
    if get is None:
        req = {**config.get("request", {}), "retries": 0, "timeout": 15}
        get = lambda url: fetch._http_get(url, req).decode("utf-8", "replace")  # noqa: E731

    def one(item: dict) -> None:
        error = None
        for url in urls_for(item):
            try:
                text = text_of(get(url))
            except Exception as exc:
                error = str(exc)
                continue
            if text:
                item["fulltext"] = text
                return
            error = "頁面沒有文字"
        item["fulltext_error"] = error

    picked = targets(items)
    with metrics.timed("enrich", items=len(picked)) as m, ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, picked))
        failed = sum("fulltext_error" in i for i in picked)
        m.update(failed=failed)
    print(f"[enrich] 補原文 {len(picked) - failed}/{len(picked)} 則", file=sys.stderr)


if __name__ == "__main__":  # 自我檢查：uv run --project packages/collect python -m newsletter_collect.enrich
    def it(uid, topic="t", rank=1.0, **kw):
        return {"uid": uid, "topic": topic, "rank": rank, "source": "Site", "url": f"https://s.example/{uid}", **kw}

    items = [it(f"n{n}", rank=10 - n) for n in range(5)] + [
        it("s1", rank=0.0, origin="search"),
        it("p_main", "ai-research", 9, source="arXiv cs.CL", url="http://arxiv.org/abs/1", venue={"status": "main"}),
        *[it(f"p{n}", "ai-research", 8 - n, source="arXiv cs.LG", url=f"http://arxiv.org/abs/{n}v1") for n in range(4)],
    ]
    assert {i["uid"] for i in targets(items)} == {"n0", "n1", "n2", "s1", "p0", "p1", "p2"}
    assert urls_for(items[-1]) == ["http://arxiv.org/html/3v1", "http://arxiv.org/abs/3v1"]

    page = ("<html><head><title>x</title><script>var a=1</script></head><body><nav>Menu Home</nav>"
            "<p>Main &amp; story</p><footer>Copyright</footer></body></html>")
    assert text_of(page) == "Main & story", text_of(page)
    assert text_of("<body><div>Subscribe now</div><article><h1>T</h1><p>Body</p></article></body>") == "T Body"

    pages = {"https://s.example/n0": page, "http://arxiv.org/abs/0v1": "<body>Authors: MIT</body>"}

    def get(url: str) -> str:
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    enrich(items, {}, get)
    by = {i["uid"]: i for i in items}
    assert by["n0"]["fulltext"] == "Main & story" and "fulltext_error" not in by["n0"]
    assert by["p0"]["fulltext"] == "Authors: MIT"  # 沒有 HTML 版就退回 abs 頁
    assert "404" in by["n1"]["fulltext_error"] and "fulltext" not in by["n3"] and "fulltext_error" not in by["n3"]
    print("ok")
