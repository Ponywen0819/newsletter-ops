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
也可以改用 `python3 src/web.py` 在網頁上按 👍／👎，直接寫同一個檔（取消記為 mark ""）。

兩條路徑共用 state/feedback.jsonl，都只往後 append、不改寫舊內容，並用同一把檔案鎖排隊，
所以可以同時跑。為了不和網頁打架：報告裡的標記只匯入「feedback.jsonl 還沒有紀錄」的那一則；
已經有紀錄的（含網頁標的、取消的）以 jsonl 為準，之後改報告裡的標記不會覆蓋它。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
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


def build_row(uid: str, mark: str, report: str, item: dict, collected_at: str | None = None) -> dict:
    """feedback.jsonl 的一列。web.py 與 collect() 共用，欄位只在這裡定義。"""
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


@contextmanager
def locked(path: Path):
    """跨程序的獨占鎖。web 與 collect 都是「先讀再 append」，要排隊才不會依過期的狀態做決定。
    不可巢狀使用（flock 對同一支程式裡的第二次開檔也會擋）。"""
    lock = path.parent / (path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as fh:  # 關檔時自動解鎖
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def append_rows(path: Path, rows: list[dict]) -> None:
    """只往後加，從不改寫舊內容。呼叫端要在 locked(path) 裡。"""
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


def selftest() -> None:
    import contextlib
    import io
    import tempfile
    import threading

    u1, u2, u3 = "1" * 16, "2" * 16, "3" * 16

    def report(marks: dict[str, str]) -> str:
        return "# t\n" + "".join(f"- x\n{mark_comment(uid, m)}\n" for uid, m in marks.items())

    def run(root: Path) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert collect(root) == 0
        return buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reports").mkdir()
        (root / "data" / "curated").mkdir(parents=True)
        out = root / "state" / "feedback.jsonl"
        (root / "reports" / "2026-09-28.md").write_text(report({u1: "+", u2: "-", u3: ""}), encoding="utf-8")

        # 第一次：匯入有填的兩則，空白的略過
        assert "新增 2、更新 0" in run(root)
        assert {u: r["mark"] for u, r in read_feedback(out).items()} == {u1: "+", u2: "-"}
        first = out.read_bytes()
        assert "新增 0、更新 0" in run(root) and out.read_bytes() == first  # 沒變動就不動檔案

        # 網頁之後把 u1 改成 -、把 u2 取消（append 兩列）：再 collect 不能蓋回報告裡的 + / -
        with locked(out):
            append_rows(out, [build_row(u1, "-", "2026-09-28.md", {}), build_row(u2, "", "2026-09-28.md", {})])
        after_web = out.read_bytes()
        assert after_web.startswith(first)  # 舊內容原封不動，只多了後面幾列
        assert "新增 0、更新 0" in run(root) and "2 筆報告裡的標記" in run(root)
        assert out.read_bytes() == after_web
        assert {u: r["mark"] for u, r in read_feedback(out).items()} == {u1: "-", u2: ""}

        # 補中繼資料：標記不變，只是把 curated 才有的 title 補上
        (root / "reports" / "2026-09-29.md").write_text(report({u3: "++"}), encoding="utf-8")
        assert "新增 1、更新 0" in run(root)
        (root / "data" / "curated" / "2026-09-29.json").write_text(
            json.dumps({"items": [{"uid": u3, "title": "補上的標題"}]}, ensure_ascii=False), encoding="utf-8")
        assert "新增 0、更新 1" in run(root)
        assert read_feedback(out)[u3]["title"] == "補上的標題" and read_feedback(out)[u3]["mark"] == "++"

        # 結尾沒換行的檔案，append 不能接在最後一列後面
        out.write_bytes(out.read_bytes().rstrip(b"\n"))
        with locked(out):
            append_rows(out, [build_row(u1, "+", "2026-09-28.md", {})])
        assert read_feedback(out)[u1]["mark"] == "+"

        # 併發：多個執行緒同時 append，collect 同時反覆跑，一列都不能掉
        base = len(out.read_text(encoding="utf-8").splitlines())
        workers, per = 6, 25
        stop = threading.Event()

        def append_many(n: int) -> None:
            for i in range(per):
                uid = f"{n:02x}{i:02x}".rjust(16, "a")
                with locked(out):
                    append_rows(out, [build_row(uid, "+", "2026-09-28.md", {})])

        def collect_loop() -> None:
            while not stop.is_set():
                run(root)

        threads = [threading.Thread(target=append_many, args=(n,)) for n in range(workers)]
        looper = threading.Thread(target=collect_loop)
        with contextlib.redirect_stdout(io.StringIO()):
            looper.start()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            stop.set()
            looper.join()
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == base + workers * per, (len(lines), base)  # collect 這時沒有東西可加
        rows = [json.loads(ln) for ln in lines]  # 每一列都是完整的 JSON
        assert len({r["uid"] for r in rows}) == len(read_feedback(out)) and len(read_feedback(out)) >= workers * per
    print("ok")


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else collect())
