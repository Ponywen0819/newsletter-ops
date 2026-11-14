"""整理層：時間窗過濾、跨日去重、近似標題合併、關鍵字評分、各主題配額。

評分拆成兩個值：
  relevance  來源保底分 + 關鍵字命中。是門檻，低於 min_relevance 不收。
  rank       relevance × 來源權重 + 新鮮度（+ feed 順序、多家報導的加分），再乘上回饋倍率。只決定排序。
拆開是為了讓「夠新」無法把不相關的東西帶進來。

arXiv 論文另看 Comments / Journal-Ref 裡的會議或期刊（config.json 的 arxiv_venues）加分，結果寫進 item["venue"]。
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from newsletter_shared import ROOT, atomic, feedback, lock

STATE = ROOT / "state" / "seen.json"
FEEDBACK = ROOT / "state" / "feedback.jsonl"
STOP = re.compile(r"[^\w一-鿿]+")
_ASCII_KW = re.compile(r"^[\x00-\x7f]+$")
_PATTERN_CACHE: dict[str, re.Pattern | None] = {}
_REJECTED = re.compile(r"reject|not accepted|declined", re.I)
_SUBMITTED = re.compile(r"submitted|under review|in submission|under submission", re.I)
# 「DocInsights at EMNLP 2026」＝掛在會議底下的 workshop；這些字接 at 則是主會議本身
_MAIN_BEFORE_AT = {"accepted", "published", "presented", "appear", "appears", "appearing",
                   "oral", "poster", "spotlight", "paper", "talk", "highlight"}
_NAMED_AT = re.compile(r"([A-Za-z][\w-]*)\s+(?:at|@)\s+$")
VENUE_BOOST = {"main": 2.0, "minor": 0.8, "submitted": 0.4}


def _norm_title(title: str) -> str:
    return STOP.sub(" ", title.lower()).strip()


def _kw_matcher(keyword: str) -> re.Pattern | None:
    """英文關鍵字用詞界比對，中文直接子字串比對（中文沒有詞界）。

    沒有這層的話，'gpu' 會命中任何含 GPU 三個字母的字串。
    結尾允許 s／es，讓 'agent' 命中 'agents'。
    # ponytail: 只處理規則複數；不規則變化（-y→-ies）或同義詞直接在 config 多列一個關鍵字
    """
    if keyword in _PATTERN_CACHE:
        return _PATTERN_CACHE[keyword]
    pattern = None
    if _ASCII_KW.match(keyword):
        pattern = re.compile(
            r"(?<![a-z0-9])" + re.escape(keyword.lower()).replace(r"\ ", r"[\s-]+") + r"(?:e?s)?(?![a-z0-9])")
    _PATTERN_CACHE[keyword] = pattern
    return pattern


def _hits(text: str, keyword: str) -> bool:
    pattern = _kw_matcher(keyword)
    return bool(pattern.search(text)) if pattern else keyword.lower() in text


def load_seen(retention_days: int = 30) -> dict:
    if not STATE.exists():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    try:
        return {k: v for k, v in json.loads(STATE.read_text(encoding="utf-8")).items() if v >= cutoff}
    except (ValueError, AttributeError, TypeError):  # 不是合法 JSON（含空檔）或不是 {uid: 時間} 的形狀
        print(f"[curate] {STATE.name} 讀不了，當作空的重來（時間窗內已報過的新聞會再出現一次）", file=sys.stderr)
        return {}


def save_seen(seen: dict) -> None:
    atomic.write_text(STATE, json.dumps(seen, ensure_ascii=False, indent=0))


def in_window(item: dict, default_hours: int) -> bool:
    """時間窗以來源為準：arXiv 週末不公告、研究部落格一週才發一篇，
    跟每天產四十則的新聞網站用同一個窗，前者永遠進不來。"""
    hours = item.get("lookback_hours") or default_hours
    if not item.get("published"):
        return True  # 無日期的來源（如 iThome）交給 seen 去重
    try:
        dt = datetime.fromisoformat(item["published"])
    except ValueError:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


def _find(names: list[str], note: str, flags: int = 0) -> tuple[int, str, str] | None:
    """在 note 裡找清單中最早出現的名稱，回傳 (位置, 名稱含年份, 清單原名)。"""
    best = None
    for name in names:
        m = re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?:\s*'?\d{2,4})?(?![A-Za-z])", note, flags)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.group(0).strip(), name)
    return best


def _find_lower(names: list[str], note: str) -> tuple[int, str, str] | None:
    """大小寫寫錯的會議名（neurips 2026）：一定要接年份才算，否則 www.example.com 會命中 WWW。
    名稱換回清單的寫法。"""
    best = None
    for name in names:
        m = re.search(r"(?<![A-Za-z])" + re.escape(name) + r"\s*('?\d{2,4})(?![A-Za-z0-9])", note, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), f"{name} {m.group(1)}", name)
    return best


def venue_of(note: str, venues: dict) -> dict | None:
    """用固定清單判斷 arXiv Comments 裡的會議/期刊，回傳
    {"status": main|minor|submitted, "kind": conference|journal, "name", "track", "label"} 或 None。

    大小寫敏感：'Nature' 不該命中 'the nature of'。
    名稱前面 30 字內有 reject 之類的字（「Rejected from ICLR」）不算那一個。
    ponytail: 清單＋正規表示式，「以前曾在某 workshop 發表」會把主會議降成 minor；少見，遇到再把寫法加進清單或規則。
    """
    if not note:
        return None
    hits = [(h, kind) for kind in ("conference", "journal")
            if (h := _find(venues.get(kind, []), note))]
    if not hits and (h := _find_lower(venues.get("conference", []), note)):
        hits = [(h, "conference")]  # 期刊不放寬：Nature 不該命中 the nature of
    hits = [(h, kind) for h, kind in hits if not _REJECTED.search(note[max(0, h[0] - 30):h[0]])]
    if not hits:
        return None
    (pos, name, _), kind = min(hits, key=lambda x: x[0][0])

    track = _find(venues.get("minor_tracks", []), note, re.I)  # track 名稱大小寫寫法不一
    track_name = track[2] if track else ""
    named = _NAMED_AT.search(note[:pos])
    if not track_name and named and named[1][0].isupper() and named[1].lower() not in _MAIN_BEFORE_AT:
        track_name = named[1]

    if _SUBMITTED.search(note):
        status, label = "submitted", f"已投稿 {name}（審查中）"
    elif track_name:
        status, label = "minor", f"{name} {track_name}（非主會議）"
    else:
        status = "main"
        label = f"已被 {name} 接受（{'主會議' if kind == 'conference' else '期刊'}）"
    return {"status": status, "kind": kind, "name": name, "track": track_name, "label": label}


# (keywords 鍵, 加減分, 是否算命中)
_KEYWORD_WEIGHTS = (
    ("boost_high", 2.0, True),
    ("boost_mid", 0.8, True),
    ("boost_low", 0.4, True),
    ("penalize", -4.0, False),
)


def _freshness(published: str) -> float:
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(published)).total_seconds() / 3600
    except ValueError:
        return 0.0
    return 1.5 * math.exp(-max(age_h, 0) / 12)


ORDER_TOP = 10  # feed 前幾則拿 order_boost：第一則全拿，往後線性遞減，第 ORDER_TOP 則起沒有


def _order_bonus(item: dict) -> float:
    """編輯排過的 feed（BBC 的頭條在最前面）：國際新聞沒有關鍵字可評分，順序是少數的訊號。"""
    return float(item.get("order_boost", 0.0)) * max(0.0, 1 - item.get("position", 0) / ORDER_TOP)


def score(item: dict, keywords: dict, venues: dict | None = None) -> tuple[float, float, list[str]]:
    """回傳 (relevance, rank, 命中的關鍵字)。"""
    text = f"{item['title']} {item['summary']}".lower()
    hits: list[str] = []
    # 高信噪比來源（官方發布、低產量研究部落格）給保底分：它們的標題常常
    # 一個技術關鍵字都沒有（「Introducing X」），但則則值得看，數量由配額控制。
    relevance = float(item.get("baseline_relevance", 0.0))

    for key, delta, is_hit in _KEYWORD_WEIGHTS:
        for kw in keywords.get(key, []):
            if _hits(text, kw):
                relevance += delta
                if is_hit:
                    hits.append(kw)

    venue = venue_of(item.get("venue_note", ""), venues or {})
    if venue:
        item["venue"] = venue
        relevance += VENUE_BOOST[venue["status"]]

    rank = relevance * float(item.get("weight", 1.0)) + _order_bonus(item)
    if item.get("published"):
        rank += _freshness(item["published"])
    return round(relevance, 2), round(rank, 2), sorted(set(hits))


_NAME = re.compile(r"[A-Z][\w'’.-]*[A-Za-z0-9]")
# 句首常見的大寫字，不是專有名詞
_NOT_NAMES = {"the", "a", "an", "how", "why", "what", "who", "when", "where", "watch", "new", "after", "before",
              "as", "in", "on", "at", "for", "with", "from", "is", "are", "was", "will", "can", "this", "that",
              "live", "breaking", "exclusive", "analysis", "opinion", "video", "says", "said"}
COVERAGE_BOOST, COVERAGE_MAX = 0.5, 2  # 每多一家報導同一則加多少 rank、最多算幾家（arXiv 的跨類別不算）
SAME_STORY_SHARED, SAME_STORY_JACCARD = 3, 0.4  # 用 2026-11-08 的國際新聞調的：同一則的各家報導都在 0.4 以上


def _names(title: str) -> set[str]:
    """標題裡的專有名詞（大寫開頭的字，去掉所有格與句首常見字），小寫。"""
    words = (re.sub(r"['’]s$", "", w).lower() for w in _NAME.findall(title))
    return {w for w in words if len(w) >= 3 and w not in _NOT_NAMES}


def same_story(a: set[str], b: set[str]) -> bool:
    """標題＋摘要裡的專有名詞夠重疊才算同一則：只共有 Trump、Iran 這種一兩個名字的不算。
    ponytail: 只看名字重疊；同一個人物的兩件不同事件、名字又多半相同時會誤併，遇到再調門檻。"""
    shared = a & b
    return len(shared) >= SAME_STORY_SHARED and len(shared) / len(a | b) >= SAME_STORY_JACCARD


def dedupe(items: list[dict], threshold: float = 0.88, name_topics: frozenset[str] = frozenset()) -> list[dict]:
    """先以 uid/url 去重，再用標題相似度合併跨來源重複報導。
    name_topics 裡的主題（國際新聞）另外用專有名詞比對：各家標題寫法不同，相似度幾乎合併不了。"""
    unique = _dedupe_urls(items)
    names = {id(i): _names(f"{i['title']}. {i.get('summary', '')}") for i in unique if i["topic"] in name_topics}

    def matches(item: dict, k: dict, norm: str) -> bool:
        if SequenceMatcher(None, norm, _norm_title(k["title"])).ratio() >= threshold:
            return True
        return (item["topic"] in name_topics and item["topic"] == k["topic"] and item["source"] != k["source"]
                and same_story(names[id(item)], names[id(k)]))

    kept: list[dict] = []
    for item in sorted(unique, key=lambda i: i.get("rank", 0), reverse=True):
        norm = _norm_title(item["title"])
        match = next((k for k in kept if matches(item, k, norm)), None)
        if match:
            match.setdefault("also_in", []).append(item["source"])
        else:
            kept.append(item)
    for item in kept:
        item["also_in"] = sorted({s for s in item.get("also_in", []) if s != item["source"]})
        if item["also_in"] and not item["source"].startswith("arXiv"):
            item["rank"] = round(item.get("rank", 0) + COVERAGE_BOOST * min(len(item["also_in"]), COVERAGE_MAX), 2)
    return kept


def _dedupe_urls(items: list[dict]) -> list[dict]:
    """同一個網址（或 uid）只留 rank 最高的，其他來源記進 also_in。"""
    by_key: dict[str, dict] = {}
    for item in items:
        key = item["url"] or item["uid"]
        existing = by_key.get(key)
        if existing is None or item.get("rank", 0) > existing.get("rank", 0):
            if existing:
                item.setdefault("also_in", []).extend(
                    [existing["source"], *existing.get("also_in", [])])
            by_key[key] = item
        else:
            existing.setdefault("also_in", []).append(item["source"])
    return list(by_key.values())


def _pick(bucket: list[dict], limit: int, per_source: int | None) -> list[dict]:
    """依 rank 取 limit 則；每個來源先最多 per_source 則，名額沒用完再照 rank 補回其他的（只有一個來源時不會少收）。"""
    if not per_source:
        return bucket[:limit]
    count: dict[str, int] = {}
    first, rest = [], []
    for item in bucket:
        n = count.get(item["source"], 0)
        (first if n < per_source else rest).append(item)
        count[item["source"]] = n + 1
    chosen = first[:limit]
    chosen += rest[:limit - len(chosen)]
    return sorted(chosen, key=lambda i: i["rank"], reverse=True)


def apply_quota(items: list[dict], config: dict) -> tuple[list[dict], dict]:
    """各主題分開取前 N 名，避免高產量來源佔滿整份報告。

    沒有這層的話，一個每天發四十則的新聞站會把研究類主題整個擠掉——
    不是因為它更相關，只是因為它量大。
    """
    quota = config.get("quota", {})
    default_quota = config.get("default_quota", 5)
    by_topic: dict[str, list[dict]] = {}
    for item in sorted(items, key=lambda i: i["rank"], reverse=True):
        by_topic.setdefault(item["topic"], []).append(item)

    kept: list[dict] = []
    dropped: dict[str, int] = {}
    per_source = config.get("max_per_source")
    for topic, bucket in by_topic.items():
        limit = quota.get(topic, default_quota)
        kept.extend(_pick(bucket, limit, per_source))
        if len(bucket) > limit:
            dropped[topic] = len(bucket) - limit

    kept.sort(key=lambda i: i["rank"], reverse=True)
    hard_cap = config.get("max_items_in_report", 30)
    if len(kept) > hard_cap:
        kept = kept[:hard_cap]
    return kept, dropped


NEAR_MISS = 3  # 配額沒滿的主題，附幾則差一點過門檻的候選給 writer


def near_misses(fresh: list[dict], ranked: list[dict], config: dict) -> list[dict]:
    """配額沒滿的主題（週末的 tech-industry 常常 0、1 則），附上沒過門檻、但有一點分數的前幾則，讓 writer 決定要不要撈回來。
    命中關鍵字的排前面，其次看 rank。arXiv 不附（量大，靠配額就夠）。不記進 seen：沒寫進報告的明天還能再出現。"""
    quota, default_quota = config.get("quota", {}), config.get("default_quota", 5)
    min_relevance = config.get("min_relevance", 1.0)
    have: dict[str, int] = {}
    for item in ranked:
        have[item["topic"]] = have.get(item["topic"], 0) + 1
    urls = {i["url"] for i in ranked}
    pool = sorted((i for i in fresh if 0 < i["relevance"] < min_relevance and i["url"] not in urls
                   and not i["source"].startswith("arXiv")),
                  key=lambda i: (bool(i["matched_keywords"]), i["rank"]), reverse=True)
    out: list[dict] = []
    for item in pool:
        topic = item["topic"]
        if have.get(topic, 0) < quota.get(topic, default_quota) and sum(i["topic"] == topic for i in out) < NEAR_MISS:
            out.append(item)
    return out


def source_boosts(config: dict) -> dict[str, float]:
    """來源 → rank 倍率，依網頁／報告上的回饋。p＝(正＋prior)/(總＋2·prior)，倍率＝1＋2·strength·(p−0.5)，
    範圍 1±strength；prior 讓少量回饋動得很少（預設 10 個 + 是 ×1.1、200 個 - 是 ×0.81）。strength 0 關閉。"""
    cfg = config.get("feedback_ranking", {})
    strength, prior = cfg.get("strength", 0.2), cfg.get("prior", 5)
    if not strength or not FEEDBACK.exists():
        return {}
    with lock.locked(FEEDBACK):
        rows = feedback.read_feedback(FEEDBACK)
    return {name: round(1 + 2 * strength * ((pos + prior) / (pos + neg + 2 * prior) - 0.5), 3)
            for name, (pos, neg) in feedback.stats(rows)["source"].items()}


def curate(items: list[dict], config: dict, redo: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict], dict]:
    """redo：同一天重跑時，上一次收錄的 uid。它們當作沒看過，否則第二次只剩第二梯隊，頭條全不見。"""
    seen = load_seen()
    for uid in redo:
        seen.pop(uid, None)
    default_hours = config.get("lookback_hours", 24)
    fresh = [i for i in items if in_window(i, default_hours) and i["uid"] not in seen]

    boosts = source_boosts(config)
    for item in fresh:
        item["relevance"], item["rank"], item["matched_keywords"] = score(
            item, config.get("keywords", {}), config.get("arxiv_venues", {}))
        if (b := boosts.get(item.get("source", ""), 1.0)) != 1.0:
            item["rank"], item["feedback_boost"] = round(item["rank"] * b, 2), b

    min_relevance = config.get("min_relevance", 1.0)
    relevant = [i for i in fresh if i["relevance"] >= min_relevance]
    ranked, dropped_by_quota = apply_quota(dedupe(relevant, name_topics=frozenset(config.get("same_story_topics", []))), config)

    now = datetime.now(timezone.utc).isoformat()
    for item in ranked:
        seen[item["uid"]] = now
    save_seen(seen)

    stats = {
        "fetched": len(items),
        "in_window_new": len(fresh),
        "passed_relevance": len(relevant),
        "after_dedupe_and_quota": len(ranked),
        "by_topic": {},
        "dropped_by_quota": dropped_by_quota,
        "near_misses": near_misses(fresh, ranked, config),  # run.py 會搬到 curated JSON 的頂層
    }
    for item in ranked:
        stats["by_topic"][item["topic"]] = stats["by_topic"].get(item["topic"], 0) + 1
    return ranked, stats
