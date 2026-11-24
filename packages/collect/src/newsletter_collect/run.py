#!/usr/bin/env python3
"""入口：uv run --project packages/collect newsletter-collect [--dry-run] [--no-search] [--lookback 48] [--list-sources] [--pool]

data/raw 只留 config.json 的 raw_retention_days 天（預設 30），沒有程式讀舊的 raw。

抓取 → 整理 → 搜尋補 RSS 弱的主題 → 補原文 → 同一件事的別家報導（cluster），寫 data/curated/<date>.json。
--pool（白天的一輪）：只把過門檻的新聞放進候選池 data/pool.json、補原文，不寫 curated、不記 seen；
晨報抓完會把候選池併進來，白天被擠出 feed 的新聞也選得到。
exit code：0 成功／2 來源設定有誤／3 一則都沒抓到。搜尋與補原文失敗只警告。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_collect import cluster as cluster_mod
from newsletter_collect import curate as curate_mod
from newsletter_collect import enrich as enrich_mod
from newsletter_collect import fetch as fetch_mod
from newsletter_collect import search as search_mod
from newsletter_collect import sources as sources_mod
from newsletter_shared import atomic, metrics
from newsletter_shared import DEFAULT_TZ, ROOT, today


INTERESTS = ROOT / "config" / "interests.md"
STALE_AFTER_DAYS = 90
RAW = ROOT / "data" / "raw"
RAW_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
POOL = ROOT / "data" / "pool.json"
POOL_DAYS = 2  # 候選池留幾天：再舊的已經在晨報的時間窗外


def interests_age() -> tuple[int | None, str]:
    """回傳 (距今天數, 提醒訊息)。檔案不存在或沒寫 updated 時天數為 None。"""
    if not INTERESTS.exists():
        return None, f"找不到 {INTERESTS.name}，report 判讀會少掉你的關注範圍"
    match = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})\s*$",
                      INTERESTS.read_text(encoding="utf-8"), re.M)
    if not match:
        return None, f"{INTERESTS.name} 裡沒有 updated: YYYY-MM-DD 這行"
    days = (date.fromisoformat(today()) - date.fromisoformat(match.group(1))).days
    if days >= STALE_AFTER_DAYS:
        return days, (f"興趣檔已 {days} 天沒更新（{match.group(1)}）——"
                      "研究重心變了的話，關鍵字多半也該跟著改")
    return days, f"興趣檔更新於 {match.group(1)}（{days} 天前）"


def load_config(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    feeds, topics, notes = sources_mod.load_sources(config_path.parent)
    config["sources"] = feeds
    config["topics"] = topics
    config["_source_notes"] = notes
    return config


def prior_uids(path: Path) -> set[str]:
    """同一天重跑時，當天 curated 上次收錄的 uid；檔案不存在或壞掉就當沒有。"""
    try:
        return {i["uid"] for i in json.loads(path.read_text(encoding="utf-8"))["items"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def list_sources(config: dict) -> None:
    for topic, label in config["topics"].items():
        feeds = [s for s in config["sources"] if s["topic"] == topic]
        print(f"\n{label}  ({topic}, {len(feeds)})")
        for src in feeds:
            target = src.get("url") or f"arxiv:{src.get('query')}"
            print(f"  - {src['name']:<28} w={src['weight']:<4} {target}")
    print(f"\n共 {len(config['sources'])} 個來源，{len(config['topics'])} 個主題。")


def append_raw(raw: list[dict], stamp: str, raw_dir: Path = RAW) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / f"{stamp}.jsonl").open("a", encoding="utf-8") as fh:
        for row in raw:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def prune_raw(stamp: str, days: int, raw_dir: Path = RAW) -> list[str]:
    """刪掉日期早於 stamp 往前 days 天的 <date>.jsonl，回傳刪掉的檔名。其他檔名不動。"""
    cutoff = date.fromisoformat(stamp) - timedelta(days=days)
    removed = []
    for path in sorted(raw_dir.glob("*.jsonl")):
        match = RAW_NAME.match(path.name)
        if match and date.fromisoformat(match.group(1)) < cutoff:
            path.unlink()
            removed.append(path.name)
    return removed


def load_pool(path: Path = POOL) -> dict[str, dict]:
    try:
        pool = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return pool if isinstance(pool, dict) else {}


def gather(raw: list[dict], config: dict, path: Path = POOL, get=None) -> dict:
    """白天的一輪：過門檻的新聞進候選池（不記 seen）、補原文（之前失敗的再試），留 POOL_DAYS 天。回傳統計。"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=POOL_DAYS)).isoformat()
    pool = {u: i for u, i in load_pool(path).items() if i.get("pooled_at", "") >= cutoff}
    added = 0
    for item in curate_mod.candidates(raw, config):
        if item["uid"] not in pool:
            pool[item["uid"]] = {**item, "pooled_at": now.isoformat()}
            added += 1
    for item in pool.values():
        item.pop("fulltext_error", None)
    enrich_mod.enrich(list(pool.values()), config, get)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(pool, ensure_ascii=False))
    return {"added": added, "pool": len(pool), "no_fulltext": sum("fulltext" not in i for i in pool.values())}


def merge_pool(raw: list[dict], pool: dict[str, dict]) -> list[dict]:
    """晨報：候選池併進這次抓到的；同一則用池裡的（已經有原文），原文之前失敗的這次再試。"""
    for item in pool.values():
        item.pop("fulltext_error", None)
    fetched = {i["uid"] for i in raw}
    return [pool.get(i["uid"], i) for i in raw] + [i for u, i in pool.items() if u not in fetched]


def collect(raw: list[dict], errors: list, config: dict, stamp: str, no_search: bool) -> None:
    """整理 → 搜尋 → 補原文 → 同一件事的別家報導，寫 data/curated/<stamp>.json。"""
    curated_path = ROOT / "data" / "curated" / f"{stamp}.json"
    redo = prior_uids(curated_path)
    if redo:
        print(f"[curate] 同日重跑：沿用上次收錄的 {len(redo)} 則重新排名", file=sys.stderr)
    with metrics.timed("curate") as m:
        ranked, stats = curate_mod.curate(raw, config, redo)
        m.update({k: stats[k] for k in ("fetched", "in_window_new", "passed_relevance",
                                        "after_dedupe_and_quota")})

    search = None
    if not no_search:
        found, search = search_mod.run(ranked, config, stamp, redo)
        ranked += found

    # 補原文；原文被擋、摘要又短（只有標題）的拿掉，名額由同主題的候補補上
    before = {i["uid"] for i in ranked}
    ranked, dropped = enrich_mod.settle(ranked, stats.pop("reserve"), config)
    curate_mod.mark_seen([i["uid"] for i in ranked if i["uid"] not in before])
    # 同一件事的別家報導留下來（不佔配額），writer 合成專題；用到的記進 seen
    stories, story_stats = cluster_mod.cluster(ranked, stats.pop("pool"), config)
    covered = {c["uid"] for s in stories for c in s["coverage"]}
    curate_mod.mark_seen(sorted(covered | {i["uid"] for i in ranked if i.get("origin") == "cluster"}))
    near, dropped_near = enrich_mod.settle([i for i in stats.pop("near_misses") if i["uid"] not in covered], [], config)
    stats["by_topic"] = {}
    for item in ranked:
        stats["by_topic"][item["topic"]] = stats["by_topic"].get(item["topic"], 0) + 1
    stats["near_misses"] = len(near)
    stats["dropped_thin"] = len(dropped)
    stats["cluster"] = {k: story_stats[k] for k in ("stories", "coverage", "promoted", "coverage_thin", "error")}
    if note := enrich_mod.dropped_note(dropped):
        errors = [*errors, note]
        print(f"[enrich] {note}", file=sys.stderr)
    payload = {
        "generated_at": datetime.now(ZoneInfo(config.get("timezone") or DEFAULT_TZ)).isoformat(),
        "stats": stats,
        "errors": errors,
        "dropped": [{k: i.get(k) for k in ("uid", "title", "url", "source", "topic", "dropped_reason")}
                    for i in dropped + dropped_near],
        "search": search,
        "near_misses": near,
        "config_digest": {k: config[k] for k in
                          ("lookback_hours", "min_relevance", "quota", "max_items_in_report")},
        "items": ranked,
        "stories": stories,  # 放最後：coverage 的原文很長，writer 讀 data/stories/ 那份就好，不必讀到這裡
    }
    curated_path.parent.mkdir(parents=True, exist_ok=True)
    curated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[curate] 收錄 {len(ranked)} 則 → {curated_path}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只抓取並印統計，不寫 state / 報告")
    ap.add_argument("--no-search", action="store_true", help="不用 LLM 搜尋補 RSS 弱的主題")
    ap.add_argument("--list-sources", action="store_true", help="列出載入的來源後結束，不連網")
    ap.add_argument("--lookback", type=int, help="覆寫時間窗（小時）")
    ap.add_argument("--pool", action="store_true", help="白天的一輪：只放進候選池 data/pool.json、補原文，不寫 curated")
    ap.add_argument("--config", default=str(ROOT / "config" / "config.json"))
    args = ap.parse_args()

    try:
        config = load_config(Path(args.config))
    except sources_mod.SourceConfigError as exc:
        print(f"來源設定有誤：{exc}", file=sys.stderr)
        return 2

    days, message = interests_age()
    prefix = "!" if (days is None or days >= STALE_AFTER_DAYS) else " "
    print(f"[interests]{prefix}{message}", file=sys.stderr)

    for note in config["_source_notes"]:
        print(f"[sources] {note}", file=sys.stderr)

    if args.list_sources:
        list_sources(config)
        return 0

    if args.lookback:
        config["lookback_hours"] = args.lookback
    tz = ZoneInfo(config.get("timezone") or DEFAULT_TZ)
    stamp = f"{datetime.now(tz):%Y-%m-%d}"

    with metrics.timed("fetch", sources=len(config["sources"])) as m:
        items, errors = fetch_mod.fetch_all(config)
        m.update(items=len(items), errors=len(errors))
    raw = [i.to_dict() for i in items]
    print(f"[fetch] {len(raw)} 則，失敗 {len(errors)} 個來源", file=sys.stderr)
    for err in errors:
        print(f"  ! {err}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps({"fetched": len(raw), "errors": errors}, ensure_ascii=False, indent=2))
        return 0

    if not raw:
        # 一則都沒抓到＝網路或來源全壞，不要留下空的 curated JSON：
        # 那會讓 writer 誤判「今天已經抓過了」而寫出空報告。
        print("[fetch] 一則都沒抓到，不寫 curated JSON。先確認網路與來源設定。",
              file=sys.stderr)
        return 3

    append_raw(raw, stamp)
    removed = prune_raw(stamp, config.get("raw_retention_days", 30))
    if removed:
        print(f"[raw] 刪掉 {len(removed)} 份超過保留天數的 raw", file=sys.stderr)
    if args.pool:
        with metrics.timed("pool") as m:
            stats = gather(raw, config)
            m.update(stats)
        print(f"[pool] 新增 {stats['added']} 則，候選池共 {stats['pool']} 則（沒有原文 {stats['no_fulltext']}）", file=sys.stderr)
        return 0
    collect(merge_pool(raw, load_pool()), errors, config, stamp, args.no_search)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
