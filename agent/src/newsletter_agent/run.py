#!/usr/bin/env python3
"""入口：uv run newsletter-fetch [--dry-run] [--no-report] [--lookback 48] [--list-sources]"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_agent import curate as curate_mod
from newsletter_agent import fetch as fetch_mod
from newsletter_agent import report as report_mod
from newsletter_agent import sources as sources_mod
from newsletter_shared import metrics
from newsletter_shared.paths import ROOT


INTERESTS = ROOT / "config" / "interests.md"
STALE_AFTER_DAYS = 90


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只抓取並印統計，不寫 state / 報告")
    ap.add_argument("--no-report", action="store_true", help="只產 curated JSON，報告交給 Claude 寫")
    ap.add_argument("--list-sources", action="store_true", help="列出載入的來源後結束，不連網")
    ap.add_argument("--lookback", type=int, help="覆寫時間窗（小時）")
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
        for topic, label in config["topics"].items():
            feeds = [s for s in config["sources"] if s["topic"] == topic]
            print(f"\n{label}  ({topic}, {len(feeds)})")
            for src in feeds:
                target = src.get("url") or f"arxiv:{src.get('query')}"
                print(f"  - {src['name']:<28} w={src['weight']:<4} {target}")
        print(f"\n共 {len(config['sources'])} 個來源，{len(config['topics'])} 個主題。")
        return 0

    if args.lookback:
        config["lookback_hours"] = args.lookback
    tz = ZoneInfo(config.get("timezone", "Asia/Taipei"))
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
        # 那會讓 news-digest 誤判「今天已經抓過了」而寫出空報告。
        print("[fetch] 一則都沒抓到，不寫 curated JSON。先確認網路與來源設定。",
              file=sys.stderr)
        return 3

    (ROOT / "data" / "raw").mkdir(parents=True, exist_ok=True)
    with (ROOT / "data" / "raw" / f"{stamp}.jsonl").open("a", encoding="utf-8") as fh:
        for row in raw:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with metrics.timed("curate") as m:
        ranked, stats = curate_mod.curate(raw, config)
        m.update({k: stats[k] for k in ("fetched", "in_window_new", "passed_relevance",
                                        "after_dedupe_and_quota")})
    payload = {
        "generated_at": datetime.now(tz).isoformat(),
        "stats": stats,
        "errors": errors,
        "config_digest": {k: config[k] for k in
                          ("lookback_hours", "min_relevance", "quota", "max_items_in_report")},
        "items": ranked,
    }
    curated_path = ROOT / "data" / "curated" / f"{stamp}.json"
    curated_path.parent.mkdir(parents=True, exist_ok=True)
    curated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[curate] 收錄 {len(ranked)} 則 → {curated_path}", file=sys.stderr)

    if not args.no_report:
        path = report_mod.write(report_mod.render(ranked, stats, config, errors), config)
        print(f"[report] {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
