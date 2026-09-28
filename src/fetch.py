"""抓取層：RSS 2.0 / Atom / arXiv API，零第三方依賴。"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import metrics

ARXIV_API = "https://export.arxiv.org/api/query"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass
class Item:
    uid: str
    title: str
    url: str
    source: str
    topic: str
    weight: float
    published: str | None  # ISO8601 UTC
    summary: str
    authors: list[str]
    lookback_hours: int | None = None  # 來源自訂的時間窗，None 表示用全域預設
    baseline_relevance: float = 0.0     # 高信噪比來源的保底相關性分數
    venue_note: str = ""                # arXiv 的 Comments + Journal-Ref，會議/期刊接受資訊在這裡

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- helpers ----------

def _http_get(url: str, cfg: dict) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": cfg.get("user_agent", "newsletter-ops/1.0"),
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip",
        },
    )
    last = None
    for attempt in range(cfg.get("retries", 2) + 1):
        try:
            with urllib.request.urlopen(req, timeout=cfg.get("timeout", 25)) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def _clean(text: str | None, limit: int = 1200) -> str:
    if not text:
        return ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = parser(value.replace("Z", "+00:00") if parser is datetime.fromisoformat else value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            continue
    return None


def canonical_url(url: str) -> str:
    """去掉追蹤參數，讓同一篇文章在不同來源能對齊。"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not (k.startswith("utm_") or k in {"ref", "source", "fbclid", "gclid", "guccounter"})
    ]
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), path,
                                    urllib.parse.urlencode(query), ""))


def make_uid(url: str, title: str) -> str:
    return hashlib.sha1(f"{canonical_url(url)}|{title.strip().lower()}".encode()).hexdigest()[:16]


# ---------- parsers ----------

def parse_feed(raw: bytes, src: dict, limit: int) -> list[Item]:
    # ponytail: RSS 1.0（RDF，如 Nature / Science）去掉預設 namespace 後欄位跟 RSS 2.0 相同，直接共用解析
    root = ET.fromstring(raw.replace(b'xmlns="http://purl.org/rss/1.0/"', b"", 1))
    items: list[Item] = []
    entries = root.findall(".//item") or root.findall(".//atom:entry", NS)
    for node in entries[:limit]:
        if node.tag.endswith("entry"):  # Atom
            title = _clean(node.findtext("atom:title", default="", namespaces=NS), 300)
            link = ""
            for ln in node.findall("atom:link", NS):
                if ln.get("rel") in (None, "alternate"):
                    link = ln.get("href", "")
                    break
            published = _parse_date(
                node.findtext("atom:published", namespaces=NS)
                or node.findtext("atom:updated", namespaces=NS)
            )
            summary = _clean(
                node.findtext("atom:summary", namespaces=NS)
                or node.findtext("atom:content", namespaces=NS)
            )
            authors = [_clean(a.findtext("atom:name", namespaces=NS), 80)
                       for a in node.findall("atom:author", NS)]
            venue_note = " | ".join(filter(None, (
                _clean(node.findtext("arxiv:comment", namespaces=NS), 300),
                _clean(node.findtext("arxiv:journal_ref", namespaces=NS), 200))))
        else:  # RSS 2.0
            title = _clean(node.findtext("title", default=""), 300)
            link = (node.findtext("link") or "").strip()
            published = _parse_date(node.findtext("pubDate") or node.findtext("dc:date", namespaces=NS))
            summary = _clean(
                node.findtext("description")
                or node.findtext("content:encoded", namespaces=NS)
            )
            authors = [a for a in [_clean(node.findtext("dc:creator", namespaces=NS), 80)] if a]
            venue_note = ""
        if not title or not link:
            continue
        items.append(Item(
            uid=make_uid(link, title), title=title, url=canonical_url(link),
            source=src["name"], topic=src.get("topic", "general"),
            weight=float(src.get("weight", 1.0)), published=published,
            summary=summary, authors=[a for a in authors if a],
            lookback_hours=src.get("lookback_hours"),
            baseline_relevance=float(src.get("baseline_relevance", 0.0)),
            venue_note=venue_note,
        ))
    return items


def fetch_arxiv(src: dict, cfg: dict, limit: int) -> list[Item]:
    params = urllib.parse.urlencode({
        "search_query": src["query"],
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": limit,
    })
    raw = _http_get(f"{ARXIV_API}?{params}", cfg)
    return parse_feed(raw, src, limit)


def fetch_source(src: dict, cfg: dict, limit: int) -> tuple[list[Item], str | None]:
    try:
        if src.get("type") == "arxiv":
            return fetch_arxiv(src, cfg, limit), None
        return parse_feed(_http_get(src["url"], cfg), src, limit), None
    except Exception as exc:  # noqa: BLE001
        return [], f"{src['name']}: {type(exc).__name__}: {exc}"


def source_host(src: dict) -> str:
    return urllib.parse.urlsplit(ARXIV_API if src.get("type") == "arxiv" else src["url"]).netloc.lower()


def wait_seconds(host: str, last_hit: dict[str, float], cfg: dict, now: float) -> float:
    """同一網域的兩次請求之間至少隔 delay 秒；不同網域不用等。
    delay 預設 delay_seconds，個別網域可在 domain_delay_seconds 覆寫（arXiv API 要求 3 秒）。"""
    if host not in last_hit:
        return 0.0
    delay = cfg.get("domain_delay_seconds", {}).get(host, cfg.get("delay_seconds", 1.0))
    return max(0.0, last_hit[host] + delay - now)


def interleave(sources: list[dict]) -> list[dict]:
    """同網域的來源輪流排開（a1 b1 c1 a2 a3 ...），同網域的等待就被其他請求的時間吸收。
    順序不影響結果，curate 會重新排序。"""
    queues: dict[str, list[dict]] = {}
    for src in sources:
        queues.setdefault(source_host(src), []).append(src)
    ordered: list[dict] = []
    while queues:
        for host in list(queues):
            ordered.append(queues[host].pop(0))
            if not queues[host]:
                del queues[host]
    return ordered


def fetch_all(config: dict) -> tuple[list[Item], list[str]]:
    cfg = config.get("request", {})
    limit = config.get("max_items_per_source", 40)
    items: list[Item] = []
    errors: list[str] = []
    last_hit: dict[str, float] = {}
    for src in interleave(config["sources"]):
        host = source_host(src)
        time.sleep(wait_seconds(host, last_hit, cfg, time.monotonic()))
        with metrics.timed("fetch_source", source=src["name"], topic=src.get("topic")) as m:
            got, err = fetch_source(src, cfg, limit)
            m.update(items=len(got), error=err)
        last_hit[host] = time.monotonic()  # 以請求結束時間起算，失敗重試也算在內
        items.extend(got)
        if err:
            errors.append(err)
    return items, errors


if __name__ == "__main__":  # 自我檢查：python3 src/fetch.py
    cfg = {"delay_seconds": 1.0, "domain_delay_seconds": {"export.arxiv.org": 3.0}}
    assert source_host({"type": "arxiv", "query": "cat:cs.LG"}) == "export.arxiv.org"
    assert source_host({"type": "rss", "url": "https://Feeds.BBCI.co.uk/news/rss.xml"}) == "feeds.bbci.co.uk"
    assert wait_seconds("a.com", {}, cfg, 100.0) == 0.0                         # 第一次請求不等
    assert wait_seconds("a.com", {"b.com": 99.9}, cfg, 100.0) == 0.0            # 別的網域不等
    assert wait_seconds("a.com", {"a.com": 99.5}, cfg, 100.0) == 0.5            # 同網域補足 1 秒
    assert wait_seconds("a.com", {"a.com": 90.0}, cfg, 100.0) == 0.0            # 早就超過間隔
    assert wait_seconds("export.arxiv.org", {"export.arxiv.org": 99.0}, cfg, 100.0) == 2.0  # arXiv 3 秒
    order = [s["url"] for s in interleave([{"url": u} for u in
             ["https://a/1", "https://a/2", "https://a/3", "https://b/1", "https://c/1"]])]
    assert order == ["https://a/1", "https://b/1", "https://c/1", "https://a/2", "https://a/3"], order
    print("ok")
