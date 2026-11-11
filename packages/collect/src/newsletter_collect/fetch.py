"""抓取層：RSS 2.0 / Atom / arXiv API，零第三方依賴。"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from xml.etree import ElementTree as ET

from newsletter_shared import metrics

ARXIV_API = "https://export.arxiv.org/api/query"
PARALLEL_HOSTS = 8  # 同時抓幾個網域；同一網域永遠依序
STALE_DAYS = 21  # 抓得到、但最新一則比這還舊：feed 可能停更（第三方 feed 不會報錯，只會不再更新）
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
    position: int = 0                   # 在 feed 裡的順序（0 起）
    order_boost: float = 0.0            # 來源設定：feed 的排列是編輯排過的，前面的加分（curate 用）

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
    attempts = cfg.get("retries", 2) + 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=cfg.get("timeout", 25)) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except Exception as exc:
            last = exc
            if not retryable(exc) or attempt == attempts - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def retryable(exc: Exception) -> bool:
    """4xx（429 除外）重試也一樣，直接放棄；連線、逾時、5xx 才重試。"""
    return not isinstance(exc, urllib.error.HTTPError) or exc.code == 429 or exc.code >= 500


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
        except Exception:
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
    skip = re.compile(src["skip"]) if src.get("skip") else None
    entries = root.findall(".//item") or root.findall(".//atom:entry", NS)
    for position, node in enumerate(entries[:limit]):
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
        if skip and (skip.search(title) or skip.search(link)):
            continue  # 來源設定的 skip：例如 BBC 的影片（/videos/、Watch:）
        items.append(Item(
            uid=make_uid(link, title), title=title, url=canonical_url(link),
            source=src["name"], topic=src.get("topic", "general"),
            weight=float(src.get("weight", 1.0)), published=published,
            summary=summary, authors=[a for a in authors if a],
            lookback_hours=src.get("lookback_hours"),
            baseline_relevance=float(src.get("baseline_relevance", 0.0)),
            venue_note=venue_note, position=position, order_boost=float(src.get("order_boost", 0.0)),
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
    except Exception as exc:
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


def stale_warning(src: dict, items: list[Item], now: datetime | None = None) -> str | None:
    """最新一則超過來源的 stale_days（預設 STALE_DAYS）天就回一句提醒；沒有日期或沒有項目不判斷。"""
    dates = [i.published for i in items if i.published]
    if not dates:
        return None
    age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(max(dates))).days
    if age <= src.get("stale_days", STALE_DAYS):
        return None
    return f"{src['name']}: 最新一則是 {age} 天前（{max(dates)[:10]}），feed 可能停更"


def by_host(sources: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for src in sources:
        groups.setdefault(source_host(src), []).append(src)
    return groups


def fetch_all(config: dict, fetch: Callable[[dict, dict, int], tuple[list[Item], str | None]] | None = None,
              ) -> tuple[list[Item], list[str]]:
    """不同網域並行、同一網域依序並守 wait_seconds 的間隔。結果照設定裡的來源順序排，和並行的先後無關。"""
    cfg = config.get("request", {})
    limit = config.get("max_items_per_source", 40)
    fetch = fetch or fetch_source
    results: dict[int, tuple[list[Item], str | None]] = {}

    def run_host(srcs: list[dict]) -> None:
        last_hit: dict[str, float] = {}
        for src in srcs:
            host = source_host(src)
            time.sleep(wait_seconds(host, last_hit, cfg, time.monotonic()))
            with metrics.timed("fetch_source", source=src["name"], topic=src.get("topic")) as m:
                got, err = fetch(src, cfg, limit)
                m.update(items=len(got), error=err)
            last_hit[host] = time.monotonic()  # 以請求結束時間起算，失敗重試也算在內
            results[id(src)] = (got, err)

    groups = by_host(config["sources"])
    with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL_HOSTS, len(groups)))) as pool:
        list(pool.map(run_host, groups.values()))
    items: list[Item] = []
    errors: list[str] = []
    for src in config["sources"]:
        got, err = results[id(src)]
        items.extend(got)
        if err or (err := stale_warning(src, got)):
            errors.append(err)
    return items, errors
