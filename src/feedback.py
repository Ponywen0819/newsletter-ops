"""回饋層：從報告裡收集人工標記，供日後調整關鍵字用。

報告每則末尾會有一行標記註解（Markdown 預覽時不顯示）：

    <!-- mark:  uid=3f9a1c2b0d4e5678 -->

看完報告時把 `mark:` 後面填上：

    +   有用，想看更多這類
    -   沒用，以後少推
    ++  很重要
    --  完全不該出現

之後跑 `python3 src/feedback.py` 收集到 state/feedback.jsonl。
同一則重複標記時以最新一次為準（以檔案日期排序）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK_RE = re.compile(r"<!--\s*mark:\s*([+-]{0,2})\s*uid=([0-9a-f]{16})\s*-->")
VALID_MARKS = {"+", "-", "++", "--"}


def mark_comment(uid: str, mark: str = "") -> str:
    return f"<!-- mark: {mark:<2} uid={uid} -->"


def scan_reports(reports_dir: Path) -> dict[str, dict]:
    """掃所有報告，回傳 uid → 標記紀錄（後面的日期覆蓋前面的）。"""
    found: dict[str, dict] = {}
    for path in sorted(reports_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in MARK_RE.finditer(text):
            mark, uid = match.group(1), match.group(2)
            if mark not in VALID_MARKS:
                continue
            found[uid] = {"uid": uid, "mark": mark, "report": path.name}
    return found


def load_curated_index(curated_dir: Path) -> dict[str, dict]:
    """uid → 該則的 title / source / topic / matched_keywords。"""
    index: dict[str, dict] = {}
    for path in sorted(curated_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"! 跳過損壞的 {path.name}", file=sys.stderr)
            continue
        for item in payload.get("items", []):
            index[item["uid"]] = item
    return index


def collect() -> int:
    marks = scan_reports(ROOT / "reports")
    if not marks:
        print("沒有找到任何標記。看完報告後把 <!-- mark: --> 填上 + 或 - 再跑一次。")
        return 0

    index = load_curated_index(ROOT / "data" / "curated")
    out = ROOT / "state" / "feedback.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["uid"]] = row

    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0
    for uid, rec in marks.items():
        item = index.get(uid, {})
        row = {
            "uid": uid,
            "mark": rec["mark"],
            "report": rec["report"],
            "collected_at": now,
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "topic": item.get("topic", ""),
            "score": item.get("score"),
            "matched_keywords": item.get("matched_keywords", []),
        }
        prior = existing.get(uid)
        if prior is None:
            added += 1
        elif prior.get("mark") != rec["mark"]:
            updated += 1
        elif not prior.get("title") and row["title"]:
            updated += 1  # 之前收集時 curated 還沒有這則，現在把中繼資料補回去
        else:
            continue
        existing[uid] = row

    with out.open("w", encoding="utf-8") as fh:
        for row in existing.values():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    tally: dict[str, int] = {}
    for row in existing.values():
        tally[row["mark"]] = tally.get(row["mark"], 0) + 1
    print(f"新增 {added}、更新 {updated}，累計 {len(existing)} 筆 → {out}")
    print("分布：" + "、".join(f"{k} {v}" for k, v in sorted(tally.items())))
    missing = sum(1 for uid in marks if uid not in index)
    if missing:
        print(f"（{missing} 筆在 curated JSON 裡找不到，只留下標記本身）")
    return 0


if __name__ == "__main__":
    raise SystemExit(collect())
