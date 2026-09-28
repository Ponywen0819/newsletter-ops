"""整理層：時間窗過濾、跨日去重、近似標題合併、關鍵字評分、各主題配額。

評分拆成兩個值，這是這一層最重要的設計：

  relevance  來源保底分 + 關鍵字命中（boost_high 2.0 / boost_mid 0.8 /
             boost_low 0.4 / penalize -4.0）。
             **這是門檻**，低於 min_relevance 的一律不收。
  rank       relevance × 來源權重 + 新鮮度加權。**這只決定排序**。

早期版本只有單一分數，結果來源權重（0.7~1.2）加新鮮度（最高 +1.5）就超過門檻，
關鍵字形同虛設——只要夠新就會被收錄。拆開之後「夠新」永遠無法讓不相關的東西進來。

arXiv 論文另外看 Comments / Journal-Ref 裡的會議或期刊（config.json 的 arxiv_venues，
conference / journal / minor_tracks 三個固定清單）：主會議或期刊 +2.0、workshop 等次級
track +0.8、僅投稿中 +0.4。arXiv 沒有審查，這是唯一便宜又可靠的品質訊號。
判斷結果連同中文 label 寫進 item["venue"]，報告直接引用，不必再判讀。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state" / "seen.json"
STOP = re.compile(r"[^\w一-鿿]+")
_ASCII_KW = re.compile(r"^[\x00-\x7f]+$")
_PATTERN_CACHE: dict[str, re.Pattern | None] = {}
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
    data = json.loads(STATE.read_text(encoding="utf-8"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    return {k: v for k, v in data.items() if v >= cutoff}


def save_seen(seen: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


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


def venue_of(note: str, venues: dict) -> dict | None:
    """用固定清單判斷 arXiv Comments 裡的會議/期刊，回傳
    {"status": main|minor|submitted, "kind": conference|journal, "name", "track", "label"} 或 None。

    大小寫敏感：'Nature' 不該命中 'the nature of'。
    ponytail: 清單＋正規表示式，「rejected from ICLR」會誤判成 main、「以前曾在某 workshop 發表」
    會把主會議降成 minor；兩者都少見，遇到再把寫法加進清單或規則。
    """
    if not note:
        return None
    hits = [(h, kind) for kind in ("conference", "journal")
            if (h := _find(venues.get(kind, []), note))]
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


def score(item: dict, keywords: dict, venues: dict | None = None) -> tuple[float, float, list[str]]:
    """回傳 (relevance, rank, 命中的關鍵字)。"""
    text = f"{item['title']} {item['summary']}".lower()
    hits: list[str] = []
    # 高信噪比來源（官方發布、低產量研究部落格）給保底分：它們的標題常常
    # 一個技術關鍵字都沒有（「Introducing X」），但則則值得看，數量由配額控制。
    relevance = float(item.get("baseline_relevance", 0.0))

    for kw in keywords.get("boost_high", []):
        if _hits(text, kw):
            relevance += 2.0
            hits.append(kw)
    for kw in keywords.get("boost_mid", []):
        if _hits(text, kw):
            relevance += 0.8
            hits.append(kw)
    for kw in keywords.get("boost_low", []):
        if _hits(text, kw):
            relevance += 0.4
            hits.append(kw)
    for kw in keywords.get("penalize", []):
        if _hits(text, kw):
            relevance -= 4.0

    venue = venue_of(item.get("venue_note", ""), venues or {})
    if venue:
        item["venue"] = venue
        relevance += VENUE_BOOST[venue["status"]]

    rank = relevance * float(item.get("weight", 1.0))
    if item.get("published"):
        try:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(item["published"])).total_seconds() / 3600
            rank += 1.5 * math.exp(-max(age_h, 0) / 12)
        except ValueError:
            pass
    return round(relevance, 2), round(rank, 2), sorted(set(hits))


def dedupe(items: list[dict], threshold: float = 0.88) -> list[dict]:
    """先以 uid/url 去重，再用標題相似度合併跨來源重複報導。"""
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

    kept: list[dict] = []
    for item in sorted(by_key.values(), key=lambda i: i.get("rank", 0), reverse=True):
        norm = _norm_title(item["title"])
        match = next(
            (k for k in kept if SequenceMatcher(None, norm, _norm_title(k["title"])).ratio() >= threshold),
            None,
        )
        if match:
            match.setdefault("also_in", []).append(item["source"])
        else:
            kept.append(item)
    for item in kept:
        item["also_in"] = sorted({s for s in item.get("also_in", []) if s != item["source"]})
    return kept


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
    for topic, bucket in by_topic.items():
        limit = quota.get(topic, default_quota)
        kept.extend(bucket[:limit])
        if len(bucket) > limit:
            dropped[topic] = len(bucket) - limit

    kept.sort(key=lambda i: i["rank"], reverse=True)
    hard_cap = config.get("max_items_in_report", 30)
    if len(kept) > hard_cap:
        kept = kept[:hard_cap]
    return kept, dropped


def curate(items: list[dict], config: dict) -> tuple[list[dict], dict]:
    seen = load_seen()
    default_hours = config.get("lookback_hours", 24)
    fresh = [i for i in items if in_window(i, default_hours) and i["uid"] not in seen]

    for item in fresh:
        item["relevance"], item["rank"], item["matched_keywords"] = score(
            item, config.get("keywords", {}), config.get("arxiv_venues", {}))

    min_relevance = config.get("min_relevance", 1.0)
    relevant = [i for i in fresh if i["relevance"] >= min_relevance]
    ranked, dropped_by_quota = apply_quota(dedupe(relevant), config)

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
    }
    for item in ranked:
        stats["by_topic"][item["topic"]] = stats["by_topic"].get(item["topic"], 0) + 1
    return ranked, stats


if __name__ == "__main__":  # 自我檢查：python3 src/curate.py
    V = {"conference": ["NeurIPS", "ICLR", "ACL", "EMNLP", "COLM"], "journal": ["Nature", "TMLR"],
         "minor_tracks": ["Workshop", "Findings", "Industry Track"]}
    def st(note):
        v = venue_of(note, V)
        return v and (v["status"], v["name"], v["track"])
    assert st("Accepted at NeurIPS 2026 (spotlight)") == ("main", "NeurIPS 2026", "")
    assert venue_of("Accepted at NeurIPS 2026", V)["label"] == "已被 NeurIPS 2026 接受（主會議）"
    assert st("Oral at ICLR 2026") == ("main", "ICLR 2026", "")
    assert st("Under review at ICLR 2027") == ("submitted", "ICLR 2027", "")
    assert st("NeurIPS 2026 Workshop on Efficient ML") == ("minor", "NeurIPS 2026", "Workshop")
    assert st("Accepted to DocInsights at EMNLP 2026") == ("minor", "EMNLP 2026", "DocInsights")
    assert st("Accepted to EMNLP 2026 Industry Track. 19 pages") == ("minor", "EMNLP 2026", "Industry Track")
    assert st("Accepted at the ICML 2026 workshop on agents".replace("ICML", "ICLR")) == ("minor", "ICLR 2026", "Workshop")
    assert st("Findings of ACL 2026") == ("minor", "ACL 2026", "Findings")
    assert st("Published in TMLR") == ("main", "TMLR", "")
    assert venue_of("Published in TMLR", V)["label"] == "已被 TMLR 接受（期刊）"
    assert st("Accepted at COLM 2026. Project website: https://x") == ("main", "COLM 2026", "")
    assert st("12 pages; on the nature of attention") is None
    assert st("ACLU report analysis") is None
    assert st("") is None
    assert _hits("openai agents tried to bruteforce", "agent") and _hits("new gpus", "gpu")
    assert _hits("ai agents ship", "ai agent") and _hits("two foundation models", "foundation model")
    assert not _hits("agentic workflows", "agent") and not _hits("agentsx", "agent")
    item = {"title": "x", "summary": "", "venue_note": "ICLR 2026 camera-ready"}
    assert score(item, {}, V)[0] == 2.0 and item["venue"]["status"] == "main"
    print("ok")
