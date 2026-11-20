import json

import pytest

from newsletter_shared import kg_flags, metrics
from newsletter_writer.kg import connect
from newsletter_writer.stories import build, prepare

DAY = "2026-11-20"


@pytest.fixture(autouse=True)
def no_metrics(monkeypatch):
    monkeypatch.setattr(metrics, "record", lambda *a, **k: None)


def item(uid, source, rank=1.0, **kw):
    return {"uid": uid, "title": f"T {uid}", "url": f"https://{source}/{uid}", "source": source, "rank": rank, **kw}


def setup(root, curated, events=(), links=()):
    """events：(id, uid, date, title, source)；links：(src, dst, kind)，都是今天建立的連結。"""
    (root / "data" / "curated").mkdir(parents=True)
    (root / "data" / "curated" / f"{DAY}.json").write_text(json.dumps(curated), encoding="utf-8")
    con = connect(root / "state" / "kg.sqlite")
    with con:
        for eid, uid, day, title, source in events:
            con.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,1)", (uid, day, title, f"https://{source}/{uid}", source))
            con.execute("INSERT INTO events(id, uid, date, summary, quote, related) VALUES (?,?,?,?,?,1)",
                        (eid, uid, day, f"事件 {eid}", "q"))
        con.executemany("INSERT INTO links VALUES (?,?,?,?,?)", [(s, d, k, "", DAY) for s, d, k in links])
    con.close()


def test_build(tmp_path):
    """collect 的分組 ＋ KG 今天的 same／follow 併組（跨主題）＋ 過去事件當前因；只有一篇又沒前因的不算專題。"""
    cover = {"uid": "c1", "title": "T c1", "url": "https://udn/c1", "source": "udn", "fulltext": "長文", "rank": 3}
    curated = {"items": [item("a", "technews", 5), item("b", "FT", 4), item("z", "BBC", 9), item("w", "NPR", 1)],
               "stories": [{"id": "s1", "uids": ["a"], "coverage": [cover]}]}
    events = [(1, "a", DAY, "T a", "technews"), (2, "b", DAY, "T b", "FT"), (3, "c1", DAY, "T c1", "udn"),
              (4, "old", "2026-11-10", "舊聞", "udn"), (5, "old2", "2026-11-12", "更舊", "CNBC"),
              (6, "w", DAY, "T w", "NPR"), (7, "gone", "2026-11-11", "被撤回", "X"), (8, "older", "2026-09-01", "太舊", "Y")]
    links = [(2, 1, "follow"), (3, 4, "same"), (2, 5, "contradict"), (2, 7, "retract"), (1, 8, "follow"), (6, 4, "follow")]
    setup(tmp_path, curated, events, links)
    got = build(tmp_path, DAY)
    assert len(got["stories"]) == 1 and "只當資料" in got["note"]
    s = got["stories"][0]
    assert s["id"] == "s1" and s["uids"] == ["a", "b"] and s["outlets"] == ["technews", "FT", "udn"]
    assert s["coverage"] == [{"uid": "c1", "title": "T c1", "url": "https://udn/c1", "source": "udn", "fulltext": "長文"}]
    # 依日期排；被今天撤回的舊事件留著（status 已失效）；超過 30 天的不附
    assert [(p["summary"], p["kind"], p["status"]) for p in s["past"]] == [
        ("事件 4", "same", "active"), ("事件 7", "retract", "invalid"), ("事件 5", "contradict", "disputed")]


def test_flagged_and_no_kg(tmp_path):
    """使用者標 wrong 的舊事件不當前因；沒有 KG 時只用 collect 的分組。"""
    curated = {"items": [item("a", "BBC")], "stories": [{"id": "s1", "uids": ["a"], "coverage": [item("c", "NPR")]}]}
    (tmp_path / "data" / "curated").mkdir(parents=True)
    (tmp_path / "data" / "curated" / f"{DAY}.json").write_text(json.dumps(curated), encoding="utf-8")
    assert [s["outlets"] for s in build(tmp_path, DAY)["stories"]] == [["BBC", "NPR"]]

    root = tmp_path / "kg"
    setup(root, {"items": [item("a", "BBC")]}, [(1, "a", DAY, "T", "BBC"), (2, "p", "2026-11-18", "x", "S"),
                                                (3, "q", "2026-11-19", "y", "S")], [(1, 2, "follow"), (1, 3, "follow")])
    assert len(build(root, DAY)["stories"]) == 1  # 一篇但有兩個前因
    kg_flags.append("q", "wrong", root / "state" / "kg_flags.jsonl")
    assert build(root, DAY)["stories"] == []


def test_prepare(tmp_path):
    (tmp_path / "data" / "stories").mkdir(parents=True)
    stale = tmp_path / "data" / "stories" / f"{DAY}.json"
    stale.write_text("old", encoding="utf-8")
    prepare(DAY, tmp_path)  # 沒有 curated：失敗不丟例外，舊檔也不留
    assert not stale.exists()
    setup(tmp_path, {"items": [item("a", "BBC")]})
    prepare(DAY, tmp_path)
    assert json.loads(stale.read_text(encoding="utf-8"))["stories"] == []
