import json
import sqlite3
from datetime import datetime

import pytest

from newsletter_shared import kg_flags, kg_store
from newsletter_shared.kg_store import sources as _sources
from newsletter_writer import kg
from newsletter_writer.kg import (
    VERSION, background, build, edition, extract, grounded, grow_pool, main, norm, parse_json, prepare, rebuild, relate,
    statuses,
)

A, B, C, D = "a" * 16, "b" * 16, "c" * 16, "d" * 16
DAY1 = [{"uid": A, "title": "Acme to buy Beta", "source": "S1", "url": "https://s1/a",
         "summary": "Acme agreed to buy Beta for $1B, people familiar said."}]
DAY2 = [{"uid": B, "title": "Beta denies deal", "source": "S2", "url": "https://s2/b",
         "fulltext": "Beta said on Monday it is not in talks with Acme and denied the report."},
        {"uid": C, "title": "Acme price", "source": "S3", "url": "https://s3/c",
         "summary": "Acme valued the deal at $2B according to a filing."}]
REPLIES = {
    "2026-01-01": {"items": [{"uid": A, "events": [
        {"summary": "Acme 同意收購 Beta", "quote": "Acme agreed to buy Beta for $1B",
         "entities": [{"name": "Acme", "type": "org"}, {"name": "Beta", "type": "org"}]},
        {"summary": "捏造", "quote": "this sentence is not in the article", "entities": [{"name": "Acme"}]},
    ]}]},
    "2026-01-02": {"items": [
        {"uid": B, "events": [{"summary": "Beta 否認與 Acme 洽談", "quote": "it is not in talks with Acme",
                               "entities": [{"name": "beta", "type": "org"}, {"name": "ACME"}]}]},
        {"uid": C, "events": [{"summary": "Acme 估值 20 億", "quote": "Acme valued the deal at $2B",
                               "entities": [{"name": "Acme", "type": "org"}]}]},
        {"uid": "f" * 16, "events": []},  # 不是今天的 uid：略過
    ]},
}


def fake(stamp: str, calls: list[str] | None = None):
    def ask(prompt: str, stage: str) -> dict:
        if calls is not None:
            calls.append(stage)
        if stage == "kg_extract":
            return REPLIES[stamp]
        pairs = json.loads(prompt.rsplit("\n\n", 1)[1])["pairs"]
        nb, nc = (next(p["new"] for p in pairs if p["uid"] == x) for x in (B, C))
        return {"links": [{"new": nb, "old": 1, "kind": "retract", "quote": "denied the report"},
                          {"new": nc, "old": 1, "kind": "contradict", "quote": "made up evidence"},  # 引句對不上
                          {"new": nc, "old": 999, "kind": "follow"}]}  # 沒提供的候選
    return ask


def test_text_helpers():
    assert norm("Open AI，Inc.") == norm("openai inc") == "openaiinc"
    assert grounded("ＡＣＭＥ agreed to buy", "Yesterday acme agreed to buy Beta.") and not grounded("acme", "acme")
    assert parse_json('好的\n{"a": 1}\n') == {"a": 1}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data" / "curated").mkdir(parents=True)
    (tmp_path / "reports").mkdir()
    for stamp, items in (("2026-01-01", DAY1), ("2026-01-02", DAY2)):
        (tmp_path / "data" / "curated" / f"{stamp}.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    (tmp_path / "reports" / "2026-01-01.md").write_text(f"x <!-- mark:    uid={A} -->", encoding="utf-8")
    return tmp_path


def test_extract(root, pg):
    """引句對不上就丟；沒有舊事件不呼叫模型；抽過的不重抽。"""
    calls: list[str] = []
    con = kg_store.connect()
    try:
        assert extract(con, "2026-01-01", DAY1, fake("2026-01-01", calls)) == {"items": 1, "extracted": 1, "dropped": 1}
        assert relate(con, "2026-01-01", {}, fake("2026-01-01", calls))["compared"] == 0
        assert extract(con, "2026-01-01", DAY1, fake("2026-01-01", calls))["items"] == 0
        assert calls == ["kg_extract"]
    finally:
        con.close()


@pytest.fixture
def built(root, pg):
    con = kg_store.connect()
    extract(con, "2026-01-01", DAY1, fake("2026-01-01"))
    con.close()
    stats = build(root, "2026-01-02", fake("2026-01-02"))
    con = kg_store.connect()
    yield root, con, stats
    con.close()


def test_build_retract_is_correction(built):
    root, con, stats = built
    assert stats["extracted"] == 2 and stats["links"] == 1 and stats["links_rejected"] == 2
    assert {r[0] for r in con.execute("SELECT key FROM mentions")} == {"acme", "beta"}  # 寫法不同算同一個實體
    bg = json.loads((root / "data" / "kg" / "2026-01-02.json").read_text(encoding="utf-8"))
    assert bg["events"] == [] and len(bg["corrections"]) == 1  # 撤回＝失效，而且報導過 → 更正
    assert bg["corrections"][0]["kind"] == "retract" and bg["corrections"][0]["by_uid"] == B


def test_flags_override(built):
    root, con, _ = built
    flags = root / "state" / "kg_flags.jsonl"
    kg_flags.append(B, "wrong", flags)  # 使用者說否認的那則才是錯的
    bg = background(con, "2026-01-02", root, kg_flags.read(flags))
    assert bg["corrections"] == [] and len(bg["events"]) == 1
    ev = bg["events"][0]
    assert ev["status"] == "active" and ev["sources"] == 1 and {"kind": "retract", "uid": B} in ev["related_today"]

    kg_flags.append(B, "", flags)
    kg_flags.append(A, "trusted", flags)
    assert statuses(con, [1], kg_flags.read(flags)) == {1: "active"}
    kg_flags.append(A, "wrong", flags)
    assert statuses(con, [1], kg_flags.read(flags)) == {1: "invalid"}


def test_contradictions(built):
    """一個來源＝有爭議，兩個來源＝失效。"""
    _, con, _ = built
    with con.transaction():
        con.execute("DELETE FROM links")
        con.execute("INSERT INTO links VALUES ((SELECT id FROM events WHERE uid=%s), 1, 'contradict', '', '2026-01-02')", (C,))
    assert statuses(con, [1], {}) == {1: "disputed"}
    with con.transaction():
        con.execute("INSERT INTO items VALUES (%s, '2026-01-02', 't', 'u', 'S4', %s)", (D, VERSION))
        eid = con.execute("INSERT INTO events(uid, date, summary, quote) VALUES (%s, '2026-01-02', 's', 'q') RETURNING id",
                          (D,)).fetchone()[0]
        con.execute("INSERT INTO links VALUES (%s, 1, 'contradict', '', '2026-01-02')", (eid,))
        con.execute("INSERT INTO links VALUES (%s, 1, 'same', '', '2026-01-02')", (eid,))
    assert statuses(con, [1], {}) == {1: "invalid"}
    assert _sources(con, 1, "S1") == 2


def test_prepare_failure_is_soft(root, pg, monkeypatch):
    """不丟例外，也不留下舊背景。"""
    def broken(prompt: str, stage: str) -> dict:
        raise RuntimeError("額度用完")
    (root / "data" / "curated" / "2026-01-03.json").write_text(json.dumps({"items": [{"uid": "e" * 16, "title": "t", "summary": "s"}]}), encoding="utf-8")
    (root / "data" / "kg").mkdir(parents=True)
    (root / "data" / "kg" / "2026-01-03.json").write_text("{}", encoding="utf-8")
    prepare("2026-01-03", "token", root, broken)
    assert not (root / "data" / "kg" / "2026-01-03.json").exists()


def test_edition(monkeypatch):
    monkeypatch.setenv("NEWSLETTER_RUN_AT", "08:00")
    assert edition(datetime(2026, 1, 1, 7, 59)) == "2026-01-01"
    assert edition(datetime(2026, 1, 1, 13, 0)) == "2026-01-02"
    assert edition(datetime(2026, 1, 1, 23, 0)) == "2026-01-02"


def test_grow_pool(root, pg):
    """候選池：有原文的才萃取，事件記在給的 edition；晨報 build 時不重抽。"""
    con = kg_store.connect()
    extract(con, "2026-01-01", DAY1, fake("2026-01-01"))
    con.close()
    (root / "data" / "pool.json").write_text(json.dumps({i["uid"]: i for i in DAY2}), encoding="utf-8")
    calls: list[str] = []

    def ask(prompt: str, stage: str) -> dict:
        calls.append(stage)
        return REPLIES["2026-01-02"] if stage == "kg_extract" else {"links": []}

    assert grow_pool(root, "2026-01-02", ask) == {"items": 1, "extracted": 1, "dropped": 0, "compared": 1,
                                                  "links": 0, "links_rejected": 0}
    con = kg_store.connect()
    try:
        assert list(con.execute("SELECT uid, date FROM events WHERE uid=%s", (B,))) == [(B, "2026-01-02")]
    finally:
        con.close()
    assert grow_pool(root, "2026-01-02", ask)["items"] == 0 and calls == ["kg_extract", "kg_relate"]


def test_rebuild_resets_sequences(built):
    """rebuild 清空四張表並重設 events／mentions 的 id 序號，不會接在舊的最大值後面。"""
    root, con, _ = built
    assert [r[0] for r in con.execute("SELECT id FROM events ORDER BY id")] == [1, 2, 3]
    assert [r[0] for r in con.execute("SELECT id FROM mentions ORDER BY id")] == [1, 2, 3, 4, 5]

    def ask(prompt: str, stage: str) -> dict:
        if stage == "kg_extract":
            uids = {i["uid"] for i in json.loads(prompt.rsplit("\n\n", 1)[1])["items"]}
            return REPLIES["2026-01-01"] if A in uids else REPLIES["2026-01-02"]
        return fake("2026-01-02")(prompt, stage)

    assert rebuild(root, ask) == 0
    assert [r[0] for r in con.execute("SELECT id FROM events ORDER BY id")] == [1, 2, 3]
    assert [r[0] for r in con.execute("SELECT id FROM mentions ORDER BY id")] == [1, 2, 3, 4, 5]


def test_import_sqlite(tmp_path, pg, monkeypatch):
    """一次性從舊 state/kg.sqlite 搬進 Postgres；mentions.id 用原本的 rowid，不需要 Claude 認證。"""
    monkeypatch.setattr(kg, "ROOT", tmp_path)
    old = tmp_path / "kg.sqlite"
    assert main(["--import-sqlite", str(old)]) == 2

    src = sqlite3.connect(old)
    src.executescript("""
        CREATE TABLE items(uid TEXT PRIMARY KEY, date TEXT, title TEXT, url TEXT, source TEXT, version INTEGER);
        CREATE TABLE events(id INTEGER PRIMARY KEY, uid TEXT, date TEXT, summary TEXT, quote TEXT, related INTEGER DEFAULT 0);
        CREATE TABLE mentions(event INTEGER, key TEXT, name TEXT, type TEXT);
        CREATE TABLE links(src INTEGER, dst INTEGER, kind TEXT, quote TEXT, date TEXT);
    """)
    with src:
        src.executemany("INSERT INTO items VALUES (?,?,?,?,?,?)", [
            (A, "2026-01-01", "T1", "https://s1/a", "S1", 1),
            (B, "2026-01-02", "T2", "https://s2/b", "S2", 1),
        ])
        src.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", [
            (1, A, "2026-01-01", "E1", "q1", 1),
            (5, B, "2026-01-02", "E2", "q2", 1),
        ])
        src.executemany("INSERT INTO mentions VALUES (?,?,?,?)", [
            (1, "acme", "Acme", "org"),
            (1, "beta", "Beta", "org"),
            (5, "acme", "ACME", "org"),
        ])
        src.execute("DELETE FROM mentions WHERE rowid = 2")  # 讓 rowid 有跳號（1, 3），確認真的沿用 rowid
        src.execute("INSERT INTO links VALUES (5, 1, 'follow', '', '2026-01-02')")
    src.close()

    assert main(["--import-sqlite", str(old)]) == 0
    con = kg_store.connect()
    try:
        assert kg_store.dump(con) == {
            "items": [
                [A, "2026-01-01", "T1", "https://s1/a", "S1", 1],
                [B, "2026-01-02", "T2", "https://s2/b", "S2", 1],
            ],
            "events": [
                [1, A, "2026-01-01", "E1", "q1", 1],
                [5, B, "2026-01-02", "E2", "q2", 1],
            ],
            "mentions": [
                [1, 1, "acme", "Acme", "org"],
                [3, 5, "acme", "ACME", "org"],
            ],
            "links": [
                [5, 1, "follow", "", "2026-01-02"],
            ],
        }
        assert con.execute("INSERT INTO events(uid) VALUES (%s) RETURNING id", (C,)).fetchone()[0] == 6
        assert con.execute("INSERT INTO mentions(event, key) VALUES (6, 'k') RETURNING id").fetchone()[0] == 4
    finally:
        con.close()


def test_ask_all():
    """多批同時問，回覆仍照 prompt 的順序；任一批失敗就整個拋出。"""
    import time

    def ask(prompt: str, stage: str) -> dict:
        time.sleep(0.05 if prompt == "a" else 0)  # 第一批最慢：結果仍要排第一
        if prompt == "boom":
            raise RuntimeError("quota")
        return {"p": prompt, "stage": stage}

    assert kg.ask_all(ask, [], "kg_extract") == []
    assert kg.ask_all(ask, ["a"], "kg_extract") == [{"p": "a", "stage": "kg_extract"}]
    assert [r["p"] for r in kg.ask_all(ask, ["a", "b", "c"], "kg_relate")] == ["a", "b", "c"]
    with pytest.raises(RuntimeError, match="quota"):
        kg.ask_all(ask, ["a", "boom"], "kg_relate")


def test_ask_all_stops_queued_batches_on_failure(monkeypatch):
    """一批失敗（多半是額度或認證）：還在排隊的批不再啟動，不白花額度。"""
    import threading

    monkeypatch.setattr(kg, "PARALLEL_ASKS", 2)
    started: list[str] = []
    gate = threading.Event()

    def ask(prompt: str, stage: str) -> dict:
        started.append(prompt)
        if prompt == "boom":
            raise RuntimeError("quota")
        gate.wait(1)  # 第一批慢：失敗發生時第三、四批還在排隊
        return {}

    with pytest.raises(RuntimeError, match="quota"):
        kg.ask_all(ask, ["slow", "boom", "later1", "later2"], "kg_extract")
    gate.set()
    assert "later2" not in started


def test_extract_relate_multi_batch(root, pg, monkeypatch):
    """每批一則、各批同時問：結果和一次問完相同；別批的 uid 出現在這批的回覆裡不收。"""
    monkeypatch.setattr(kg, "EXTRACT_BATCH", 1)
    monkeypatch.setattr(kg, "RELATE_BATCH", 1)
    con = kg_store.connect()
    try:
        assert extract(con, "2026-01-01", DAY1, fake("2026-01-01"))["extracted"] == 1
        stats = extract(con, "2026-01-02", DAY2, fake("2026-01-02"))
        assert stats == {"items": 2, "extracted": 2, "dropped": 0}  # 回覆含另一批的 uid：只收這批的，不重複
        assert con.execute("SELECT COUNT(*) FROM events WHERE date='2026-01-02'").fetchone()[0] == 2
    finally:
        con.close()


def test_reextract_forgets_old_events(root, pg, monkeypatch):
    """萃取器版本變了：同一則重抽，舊事件連同實體與連結一起刪掉。"""
    con = kg_store.connect()
    try:
        extract(con, "2026-01-01", DAY1, fake("2026-01-01"))
        extract(con, "2026-01-02", DAY2, fake("2026-01-02"))
        relate(con, "2026-01-02", {i["uid"]: kg.article(i) for i in DAY2}, fake("2026-01-02"))
        assert con.execute("SELECT COUNT(*) FROM links").fetchone()[0] > 0
        monkeypatch.setattr(kg, "VERSION", kg.VERSION + 1)
        extract(con, "2026-01-01", DAY1, fake("2026-01-01"))
        old = con.execute("SELECT COUNT(*) FROM events WHERE uid=%s AND id=1", (A,)).fetchone()[0]
        assert old == 0  # 原本的 1 號事件不在了
        assert con.execute("SELECT COUNT(*) FROM links WHERE src=1 OR dst=1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM mentions WHERE event=1").fetchone()[0] == 0
    finally:
        con.close()


def test_candidates_limit_and_order(pg):
    """候選依共同實體數、日期、id 排，各取 CANDIDATES 個；同一則與比自己晚的不算。"""
    con = kg_store.connect()
    try:
        with con.transaction():
            for n in range(8):
                uid = f"{n:016x}"
                con.execute("INSERT INTO items VALUES (%s, '2026-01-01', 't', 'u', 's', 1)", (uid,))
                eid = con.execute("INSERT INTO events(uid, date, summary, quote, related) VALUES (%s, '2026-01-01', %s, 'q', 1) "
                                  "RETURNING id", (uid, f"e{n}")).fetchone()[0]
                keys = ["acme", "beta"] if n % 2 else ["acme"]
                for k in keys:
                    con.execute("INSERT INTO mentions(event, key, name, type) VALUES (%s, %s, %s, 'org')", (eid, k, k))
            con.execute("INSERT INTO items VALUES ('ffffffffffffffff', '2026-01-02', 't', 'u', 's', 1)")
            new = con.execute("INSERT INTO events(uid, date, summary, quote) VALUES ('ffffffffffffffff', '2026-01-02', 'new', 'q') "
                              "RETURNING id").fetchone()[0]
            for k in ("acme", "beta"):
                con.execute("INSERT INTO mentions(event, key, name, type) VALUES (%s, %s, %s, 'org')", (new, k, k))
        got = kg._candidates(con, [new], "2026-01-02")[new]
        # 共同兩個實體的（奇數號）先，同分時 id 大的（較新）先；最多 CANDIDATES 個
        assert [c["summary"] for c in got] == ["e7", "e5", "e3", "e1", "e6"][:kg.CANDIDATES]
        assert kg._candidates(con, [], "2026-01-02") == {}
    finally:
        con.close()
