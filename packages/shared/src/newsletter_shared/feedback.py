"""回饋層：收集報告裡的人工標記到 state/feedback.jsonl。

報告每則末尾有 `<!-- mark:  uid=... -->`，在 `mark:` 後填 + / - / ++ / --，再跑 `uv run --project packages/shared newsletter-feedback`。
網頁的 👍／👎 寫同一個檔；兩邊都只 append、共用同一把鎖。
報告裡的標記只匯入 jsonl 還沒有紀錄的那一則，已有紀錄的以 jsonl 為準。
`newsletter-feedback --stats` 依來源／主題／關鍵字統計，給調 config 與 interests.md 參考。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from newsletter_shared.lock import locked
from newsletter_shared import ROOT

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
        for item in [*payload.get("near_misses", []), *payload.get("items", [])]:  # writer 可能撈回 near_misses
            index[item["uid"]] = item
    return index


def build_row(uid: str, mark: str, report: str, item: dict, collected_at: str | None = None) -> dict:
    """feedback.jsonl 的一列。newsletter-web 與 collect() 共用，欄位只在這裡定義。"""
    return {
        "uid": uid,
        "mark": mark,
        "report": report,
        "collected_at": collected_at or datetime.now(timezone.utc).isoformat(),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "topic": item.get("topic", ""),
        "score": item.get("score"),
        "matched_keywords": item.get("matched_keywords", []),
    }


def read_feedback(path: Path) -> dict[str, dict]:
    """讀 feedback.jsonl，同一個 uid 以最後一筆為準（檔案是 append 出來的）。"""
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["uid"]] = row
    return rows


def append_rows(path: Path, rows: list[dict]) -> None:
    """只往後加，從不改寫舊內容。web 與 collect 都是「先讀再 append」，呼叫端要在 lock.locked(path) 裡排隊，
    才不會依過期的狀態做決定。"""
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab+") as fh:
        size = fh.seek(0, os.SEEK_END)
        if size:
            fh.seek(size - 1)
            if fh.read(1) != b"\n":  # 手改過、結尾沒換行：不要接在上一列後面
                data = b"\n" + data
        fh.write(data)


def collect(root: Path = ROOT) -> int:
    marks = scan_reports(root / "reports")
    if not marks:
        print("沒有找到任何標記。看完報告後把 <!-- mark: --> 填上 + 或 - 再跑一次。")
        return 0

    index = load_curated_index(root / "data" / "curated")
    out = root / "state" / "feedback.jsonl"

    now = datetime.now(timezone.utc).isoformat()
    added = updated = ignored = 0
    new_rows: list[dict] = []
    with locked(out):
        existing = read_feedback(out)
        for uid, rec in marks.items():
            item = index.get(uid, {})
            prior = existing.get(uid)
            if prior is None:
                row = build_row(uid, rec["mark"], rec["report"], item, now)
                added += 1
            elif prior.get("mark") != rec["mark"]:
                ignored += 1  # jsonl 已有不同的紀錄（多半是網頁標的），以它為準
                continue
            elif not prior.get("title") and item.get("title"):
                row = build_row(uid, prior["mark"], rec["report"], item, now)
                updated += 1  # 之前收集時 curated 還沒有這則，現在把中繼資料補回去
            else:
                continue
            new_rows.append(row)
            existing[uid] = row
        if new_rows:
            append_rows(out, new_rows)

    tally: dict[str, int] = {}
    for row in existing.values():
        label = row["mark"] or "已取消"
        tally[label] = tally.get(label, 0) + 1
    print(f"新增 {added}、更新 {updated}，累計 {len(existing)} 筆 → {out}")
    print("分布：" + "、".join(f"{k} {v}" for k, v in sorted(tally.items())))
    missing = sum(1 for uid in marks if uid not in index)
    if missing:
        print(f"（{missing} 筆在 curated JSON 裡找不到，只留下標記本身）")
    if ignored:
        print(f"（{ignored} 筆報告裡的標記和 feedback.jsonl 已有的紀錄不同，沒有覆蓋；要改請用網頁）")
    return 0


def stats(rows: dict[str, dict]) -> dict[str, dict[str, list[int]]]:
    """分組 → 名稱 → [正面, 負面]。每個 uid 只算最後一筆，取消的不算；缺欄位的歸到「未知」。"""
    out: dict[str, dict[str, list[int]]] = {"source": {}, "topic": {}, "keyword": {}}
    for row in rows.values():
        mark = row.get("mark", "")
        if mark not in VALID_MARKS:
            continue
        side = 0 if mark.startswith("+") else 1
        for group, names in (("source", [row.get("source")]), ("topic", [row.get("topic")]),
                             ("keyword", row.get("matched_keywords") or [None])):
            for name in names:
                out[group].setdefault(name or "未知", [0, 0])[side] += 1
    return out


def print_stats(path: Path) -> int:
    rows = read_feedback(path)
    labels = {"source": "來源", "topic": "主題", "keyword": "關鍵字"}
    for group, counts in stats(rows).items():
        print(f"\n{labels[group]}（+ / -）")
        for name, (pos, neg) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0])):
            print(f"  {name:<32} {pos:>3} / {neg:<3} {pos / (pos + neg):>4.0%}")
    if not rows:
        print("還沒有任何回饋。")
    return 0


def main() -> int:
    if "--stats" in sys.argv[1:]:
        return print_stats(ROOT / "state" / "feedback.jsonl")
    return collect()


if __name__ == "__main__":
    raise SystemExit(main())
