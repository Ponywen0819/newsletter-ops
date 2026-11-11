import contextlib
import io
import json
import threading

import pytest

from newsletter_shared.feedback import append_rows, build_row, collect, mark_comment, read_feedback, stats
from newsletter_shared.lock import locked

U1, U2, U3 = "1" * 16, "2" * 16, "3" * 16


def report(marks: dict[str, str]) -> str:
    return "# t\n" + "".join(f"- x\n{mark_comment(uid, m)}\n" for uid, m in marks.items())


def run(root) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert collect(root) == 0
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "data" / "curated").mkdir(parents=True)
    (tmp_path / "reports" / "2026-09-28.md").write_text(report({U1: "+", U2: "-", U3: ""}), encoding="utf-8")
    return tmp_path


def out_of(root):
    return root / "state" / "feedback.jsonl"


def test_collect(root):
    out = out_of(root)
    # 第一次：匯入有填的兩則，空白的略過
    assert "新增 2、更新 0" in run(root)
    assert {u: r["mark"] for u, r in read_feedback(out).items()} == {U1: "+", U2: "-"}
    first = out.read_bytes()
    assert "新增 0、更新 0" in run(root) and out.read_bytes() == first

    # 網頁之後把 U1 改成 -、把 U2 取消（append 兩列）：再 collect 不能蓋回報告裡的 + / -
    with locked(out):
        append_rows(out, [build_row(U1, "-", "2026-09-28.md", {}), build_row(U2, "", "2026-09-28.md", {})])
    after_web = out.read_bytes()
    assert after_web.startswith(first)
    assert "新增 0、更新 0" in run(root) and "2 筆報告裡的標記" in run(root)
    assert out.read_bytes() == after_web
    assert {u: r["mark"] for u, r in read_feedback(out).items()} == {U1: "-", U2: ""}

    # 補中繼資料：標記不變，只是把 curated 才有的 title 補上
    (root / "reports" / "2026-09-29.md").write_text(report({U3: "++"}), encoding="utf-8")
    assert "新增 1、更新 0" in run(root)
    (root / "data" / "curated" / "2026-09-29.json").write_text(
        json.dumps({"items": [{"uid": U3, "title": "補上的標題"}]}, ensure_ascii=False), encoding="utf-8")
    assert "新增 0、更新 1" in run(root)
    assert read_feedback(out)[U3]["title"] == "補上的標題" and read_feedback(out)[U3]["mark"] == "++"


def test_append_after_missing_newline(root):
    """結尾沒換行的檔案，append 不能接在最後一列後面。"""
    out = out_of(root)
    run(root)
    out.write_bytes(out.read_bytes().rstrip(b"\n"))
    with locked(out):
        append_rows(out, [build_row(U1, "-", "2026-09-28.md", {})])
    assert read_feedback(out)[U1]["mark"] == "-"


def test_concurrent_append(root):
    """多個執行緒同時 append，collect 同時反覆跑，一列都不能掉。"""
    out = out_of(root)
    run(root)
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
    assert len(lines) == base + workers * per
    rows = [json.loads(ln) for ln in lines]
    assert len({r["uid"] for r in rows}) == len(read_feedback(out)) and len(read_feedback(out)) >= workers * per


def test_stats():
    """只算最後一筆、取消的不算、++ 算正面、缺欄位歸「未知」。"""
    item = {"source": "BBC", "topic": "world", "matched_keywords": ["npu", "edge ai"]}
    rows_in = [build_row(U1, "-", "r", item), build_row(U1, "++", "r", item), build_row(U2, "--", "r", item),
               build_row(U3, "", "r", item), build_row("4" * 16, "+", "r", {})]
    got = stats({r["uid"]: r for r in rows_in})
    assert got["source"] == {"BBC": [1, 1], "未知": [1, 0]}
    assert got["topic"] == {"world": [1, 1], "未知": [1, 0]}
    assert got["keyword"] == {"npu": [1, 1], "edge ai": [1, 1], "未知": [1, 0]}
