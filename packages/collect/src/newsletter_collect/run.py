#!/usr/bin/env python3
"""入口：uv run --project packages/collect newsletter-collect [--dry-run] [--no-search] [--lookback 48] [--list-sources]

data/raw 只留 config.json 的 raw_retention_days 天（預設 30），沒有程式讀舊的 raw。

抓取 → 整理 → 搜尋補 RSS 弱的主題 → 補原文，寫 data/curated/<date>.json。
exit code：0 成功／2 來源設定有誤／3 一則都沒抓到。搜尋與補原文失敗只警告。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_collect import curate as curate_mod
from newsletter_collect import enrich as enrich_mod
from newsletter_collect import fetch as fetch_mod
from newsletter_collect import search as search_mod
from newsletter_collect import sources as sources_mod
from newsletter_shared import metrics
from newsletter_shared import DEFAULT_TZ, ROOT


INTERESTS = ROOT / "config" / "interests.md"
STALE_AFTER_DAYS = 90
RAW = ROOT / "data" / "raw"
RAW_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def interests_age() -> tuple[int | None, str]:
    """回傳 (距今天數, 提醒訊息)。檔案不存在或沒寫 updated 時天數為 None。"""
    if not INTERESTS.exists():
        return None, f"找不到 {INTERESTS.name}，report 判讀會少掉你的關注範圍"
    match = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})\s*$",
                      INTERESTS.read_text(encoding="utf-8"), re.M)
    if not match:
        return None, f"{INTERESTS.name} 裡沒有 updated: YYYY-MM-DD 這行"
    days = (date.today() - date.fromisoformat(match.group(1))).days
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


def selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        raw_dir = Path(d)
        for name in ("2026-10-08.jsonl", "2026-10-09.jsonl", "2026-11-08.jsonl", "notes.jsonl", "2026-01-01.txt"):
            (raw_dir / name).write_text("", encoding="utf-8")
        assert prune_raw("2026-11-08", 30, raw_dir) == ["2026-10-08.jsonl"]
        assert sorted(p.name for p in raw_dir.iterdir()) == [
            "2026-01-01.txt", "2026-10-09.jsonl", "2026-11-08.jsonl", "notes.jsonl"]
        assert prune_raw("2026-11-08", 30, raw_dir) == []
    print("ok")


def collect(raw: list[dict], errors: list, config: dict, stamp: str, no_search: bool) -> None:
    """整理 → 搜尋 → 補原文，寫 data/curated/<stamp>.json。"""
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
        for item in found:
            stats["by_topic"][item["topic"]] = stats["by_topic"].get(item["topic"], 0) + 1
    enrich_mod.enrich(ranked, config)

    near = stats.pop("near_misses")
    stats["near_misses"] = len(near)
    payload = {
        "generated_at": datetime.now(ZoneInfo(config.get("timezone") or DEFAULT_TZ)).isoformat(),
        "stats": stats,
        "errors": errors,
        "search": search,
        "near_misses": near,
        "config_digest": {k: config[k] for k in
                          ("lookback_hours", "min_relevance", "quota", "max_items_in_report")},
        "items": ranked,
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
    ap.add_argument("--config", default=str(ROOT / "config" / "config.json"))
    ap.add_argument("--selftest", action="store_true", help="不連網的自我檢查")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0

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
    collect(raw, errors, config, stamp, args.no_search)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
