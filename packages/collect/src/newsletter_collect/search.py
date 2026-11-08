"""搜尋層：RSS 弱的主題交給 Claude 用 WebSearch 補，程式驗收後才併進 curated。

要補哪些主題由程式決定（config 的 search.always，加上 curate 後 0 則的主題），不讓 LLM 自己判斷。
LLM 只開 WebSearch，結果以固定格式的 JSON 回覆；每則由程式自己抓網址，確認連得到、標題對得上、時間在窗內。
搜過的 query、LLM 的原始回覆、收下與退回（含原因）的項目都寫進 data/search/<date>.json。
失敗只警告：搜尋是補充，不擋後面的流程。
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Callable

from newsletter_collect import curate, fetch
from newsletter_shared import ROOT, atomic

TOOLS = ["WebSearch"]
REQUIRED = ("topic", "title", "url", "published", "query")
TITLE_TAGS = re.compile(r"<title[^>]*>(.*?)</title>|<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']*)",
                        re.S | re.I)

PROMPT = """你是新聞搜尋助手，只能用 WebSearch。現在時間 {now}。
下面這些主題的 RSS 來源今天收錄不足，請為每個主題找最多 {per_gap} 則過去 {hours} 小時內最重要的新聞。

{topics}

規則：
- 只收新聞事件本身的報導頁，不要首頁、分類頁、影片頁、評論或彙整。
- url 必須是你在搜尋結果裡看到的原文網址，不要自己拼湊；title 照抄原文標題，不要翻譯。
- 不要和已列出的候選重複；候選裡已經有當天最重要的事件，就不用補。
- 找不到夠重要的就少給或不給，不要湊數。

最後一則回覆只輸出 JSON，不加說明、不加 code fence：
{{"items": [{{"topic": "主題代碼", "title": "原文標題", "url": "https://...", "published": "ISO 8601，含時區", "summary": "一兩句繁體中文摘要", "query": "找到它的那次搜尋字串"}}]}}"""


def gaps(ranked: list[dict], config: dict) -> list[str]:
    have = Counter(i["topic"] for i in ranked)
    always = config.get("search", {}).get("always", [])
    return [t for t in config["topics"] if t in always or not have[t]]


def prompt(topics: list[str], ranked: list[dict], config: dict, now: datetime) -> str:
    cfg = config.get("search", {})
    lines = []
    for t in topics:
        have = [i["title"] for i in ranked if i["topic"] == t][:10]
        lines.append(f"- {t}（{config['topics'][t]}）：目前候選 {len(have)} 則")
        lines += [f"  - {title}" for title in have]
    return PROMPT.format(now=now.isoformat(timespec="minutes"), per_gap=cfg.get("per_gap", 2),
                         hours=cfg.get("lookback_hours", 48), topics="\n".join(lines))


def parse(text: str) -> list:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("回覆裡沒有 JSON")
    items = json.loads(text[start:end + 1]).get("items")
    if not isinstance(items, list):
        raise ValueError("JSON 沒有 items 陣列")
    return items


def _norm(text: str) -> str:
    return curate._norm_title(html.unescape(text))


def title_matches(claimed: str, page: str) -> bool:
    """頁面的 <title> 或 og:title（含去掉「| 網站名」的片段）和宣稱的標題夠像才算。"""
    want = _norm(claimed)
    for m in TITLE_TAGS.finditer(page):
        full = m.group(1) or m.group(2) or ""
        for cand in {full, *re.split(r"\s+[|\-–—]\s+", full)}:
            got = _norm(cand)
            if got and (SequenceMatcher(None, want, got).ratio() >= 0.6 or (len(got) >= 8 and got in want)
                        or (len(want) >= 8 and want in got)):
                return True
    return False


def check(item, topics: list[str], hours: int, now: datetime) -> str | None:
    """不連網的檢查，回傳退回原因；None 表示通過。"""
    if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in REQUIRED):
        return "欄位不齊"
    if item["topic"] not in topics:
        return "主題不在這次要補的清單"
    if not item["url"].startswith(("https://", "http://")):
        return "網址不是 http(s)"
    published = fetch._parse_date(item["published"])
    if published is None:
        return "發佈時間無法解析"
    age = now - datetime.fromisoformat(published)
    if age > timedelta(hours=hours) or age < -timedelta(hours=1):
        return f"發佈時間不在 {hours} 小時內"
    return None


def check_page(item: dict, get: Callable[[str], str]) -> str | None:
    try:
        page = get(item["url"])
    except Exception as exc:
        return f"網址抓不到：{exc}"
    return None if title_matches(item["title"], page) else "頁面標題對不上"


def accept(found: list, ranked: list[dict], seen: dict, config: dict,
           get: Callable[[str], str]) -> tuple[list[dict], list[dict]]:
    """驗收 LLM 的回覆，回傳 (收下的 curated 項目, 退回的 {item, reason})。"""
    topics, now = gaps(ranked, config), datetime.now(timezone.utc)
    cfg = config.get("search", {})
    hours, per_gap = cfg.get("lookback_hours", 48), cfg.get("per_gap", 2)
    urls = {fetch.canonical_url(i["url"]) for i in ranked}
    kept: list[dict] = []
    rejected: list[dict] = []
    for raw in found:
        reason = check(raw, topics, hours, now)
        if reason is None:
            url = fetch.canonical_url(raw["url"])
            uid = fetch.make_uid(raw["url"], raw["title"])
            if url in urls:
                reason = "和已收錄的項目重複"
            elif uid in seen:
                reason = "前幾天已收錄過"
            elif sum(i["topic"] == raw["topic"] for i in kept) >= per_gap:
                reason = f"超過每個主題 {per_gap} 則"
            else:
                reason = check_page(raw, get)  # 最後才連網：前面擋得掉的就不必抓
        if reason:
            rejected.append({"item": raw, "reason": reason})
            continue
        urls.add(url)
        kept.append({
            "uid": uid, "title": raw["title"].strip(), "url": raw["url"].strip(),
            "source": "搜尋：" + urllib.parse.urlsplit(raw["url"]).netloc.removeprefix("www."),
            "topic": raw["topic"], "weight": 1.0, "published": fetch._parse_date(raw["published"]),
            "summary": str(raw.get("summary", "")).strip(), "authors": [],
            "origin": "search", "query": raw["query"].strip(),
            "relevance": 0.0, "rank": 0.0, "matched_keywords": [], "also_in": [],
        })
    return kept, rejected


def _get(config: dict) -> Callable[[str], str]:
    req = {**config.get("request", {}), "retries": 0, "timeout": 15}
    return lambda url: fetch._http_get(url, req).decode("utf-8", "replace")


def run(ranked: list[dict], config: dict, stamp: str, redo: set[str]) -> tuple[list[dict], dict]:
    """回傳 (收下的項目, 寫進 curated 的摘要)。紀錄寫到 data/search/<stamp>.json。"""
    from newsletter_llm import runner

    topics = gaps(ranked, config)
    now = datetime.now(timezone.utc)
    log: dict = {"generated_at": now.isoformat(timespec="seconds"), "gaps": topics, "queries": [],
                 "response": None, "accepted": [], "rejected": [], "error": None}
    kept: list[dict] = []
    if topics:
        print(f"[search] 要補的主題：{', '.join(topics)}", file=sys.stderr)
        auth = runner.pick_auth("search") if runner.sdk is not None else None
        if runner.sdk is None:
            log["error"] = "缺 claude-agent-sdk"
        elif auth is None:
            log["error"] = "沒有可用的 OAuth token"
        else:
            cfg = config.get("search", {})
            usage, sdk_error, wall = runner.run(prompt(topics, ranked, config, now), tools=TOOLS,
                                                max_turns=cfg.get("max_turns", 20), token=auth.token, tag="search")
            fail = runner.failure(usage, sdk_error)
            if usage.result is not None:
                runner.log_usage(usage, wall, fail[1] if fail else "ok", "search", "search")
            log["queries"] = [c["input"].get("query") for c in usage.calls if c["tool"] == "WebSearch"]
            if fail:
                log["error"] = fail[2]
            else:
                log["response"] = usage.result.result
                try:
                    seen = {k: v for k, v in curate.load_seen().items() if k not in redo}
                    kept, log["rejected"] = accept(parse(usage.result.result or ""), ranked, seen, config, _get(config))
                except ValueError as exc:
                    log["error"] = f"回覆格式不符：{exc}"
        log["accepted"] = kept
        if kept:
            seen = curate.load_seen()
            seen.update({i["uid"]: now.isoformat() for i in kept})
            curate.save_seen(seen)
    path = ROOT / "data" / "search" / f"{stamp}.json"
    atomic.write_text(path, json.dumps(log, ensure_ascii=False, indent=2))
    print(f"[search] 收下 {len(kept)} 則、退回 {len(log['rejected'])} 則 → {path}", file=sys.stderr)
    if log["error"]:
        print(f"[search] ! 搜尋失敗，照常往下走：{log['error']}", file=sys.stderr)
    return kept, {"gaps": topics, "accepted": len(kept), "rejected": len(log["rejected"]), "error": log["error"]}


if __name__ == "__main__":  # 自我檢查：uv run --project packages/collect python -m newsletter_collect.search
    now = datetime.now(timezone.utc)
    config = {"topics": {"world": "國際新聞", "science": "科學", "economy": "商業"},
              "search": {"always": ["world"], "per_gap": 1, "lookback_hours": 48}}
    ranked = [{"topic": "world", "title": "Old headline", "url": "https://bbc.example/a?utm_source=x"},
              {"topic": "economy", "title": "Rates", "url": "https://e.example/r"}]
    assert gaps(ranked, config) == ["world", "science"]  # world 固定補；science 0 則；economy 有東西
    p = prompt(["world", "science"], ranked, config, now)
    assert "world（國際新聞）：目前候選 1 則" in p and "  - Old headline" in p and "science（科學）：目前候選 0 則" in p

    assert parse('好的\n{"items": [{"a": 1}]}\n') == [{"a": 1}]
    for bad in ("沒有", '{"x": 1}', '{"items": {}}'):
        try:
            parse(bad)
            raise AssertionError(bad)
        except ValueError:
            pass

    page = '<html><head><title>Ceasefire agreed in talks | Reuters</title></head></html>'
    assert title_matches("Ceasefire agreed in talks", page)
    assert title_matches("Ceasefire agreed in talks", '<meta property="og:title" content="Ceasefire agreed in talks &amp; more">')
    assert not title_matches("Something else entirely", page)

    pages = {"https://r.example/ok": page, "https://r.example/ok2": page}

    def get(url: str) -> str:
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    def item(**kw) -> dict:
        return {"topic": "world", "title": "Ceasefire agreed in talks", "url": "https://r.example/ok",
                "published": (now - timedelta(hours=3)).isoformat(), "summary": "s", "query": "q", **kw}

    found = [item(), item(url="https://r.example/ok2"), item(topic="economy"), item(url="ftp://x"),
             item(published="昨天"), item(published=(now - timedelta(hours=49)).isoformat()), item(topic="science", url="https://r.example/404"),
             item(topic="science", title="Wrong title here", url="https://r.example/ok2"), item(url="https://bbc.example/a"),
             {"topic": "world"}, "not a dict",
             item(topic="science", url="https://r.example/seen")]
    seen = {fetch.make_uid("https://r.example/seen", "Ceasefire agreed in talks"): "x"}
    pages["https://r.example/seen"] = page
    kept, rejected = accept(found, ranked, seen, config, get)
    assert [i["url"] for i in kept] == ["https://r.example/ok"], kept
    k = kept[0]
    assert k["origin"] == "search" and k["source"] == "搜尋：r.example" and k["query"] == "q" and len(k["uid"]) == 16
    assert k["published"] == fetch._parse_date((now - timedelta(hours=3)).isoformat()), k
    reasons = [r["reason"] for r in rejected]
    assert reasons == ["超過每個主題 1 則", "主題不在這次要補的清單", "網址不是 http(s)", "發佈時間無法解析",
                       "發佈時間不在 48 小時內", "網址抓不到：404", "頁面標題對不上", "和已收錄的項目重複",
                       "欄位不齊", "欄位不齊", "前幾天已收錄過"], reasons
    print("ok")
