import json

import pytest

from newsletter_shared import kg_flags
from newsletter_writer.kg import (
    VERSION, _sources, background, build, connect, extract, grounded, norm, parse_json, prepare, relate, statuses,
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


def test_extract(root):
    """引句對不上就丟；沒有舊事件不呼叫模型；抽過的不重抽。"""
    calls: list[str] = []
    con = connect(root / "state" / "kg.sqlite")
    assert extract(con, "2026-01-01", DAY1, fake("2026-01-01", calls)) == {"items": 1, "extracted": 1, "dropped": 1}
    assert relate(con, "2026-01-01", {}, fake("2026-01-01", calls))["compared"] == 0
    assert extract(con, "2026-01-01", DAY1, fake("2026-01-01", calls))["items"] == 0
    assert calls == ["kg_extract"]
    con.close()


@pytest.fixture
def built(root):
    con = connect(root / "state" / "kg.sqlite")
    extract(con, "2026-01-01", DAY1, fake("2026-01-01"))
    con.close()
    stats = build(root, "2026-01-02", fake("2026-01-02"))
    con = connect(root / "state" / "kg.sqlite")
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
    with con:
        con.execute("DELETE FROM links")
        con.execute("INSERT INTO links VALUES ((SELECT id FROM events WHERE uid=?), 1, 'contradict', '', '2026-01-02')", (C,))
    assert statuses(con, [1], {}) == {1: "disputed"}
    with con:
        con.execute("INSERT INTO items VALUES (?, '2026-01-02', 't', 'u', 'S4', ?)", (D, VERSION))
        eid = con.execute("INSERT INTO events(uid, date, summary, quote) VALUES (?, '2026-01-02', 's', 'q')", (D,)).lastrowid
        con.execute("INSERT INTO links VALUES (?, 1, 'contradict', '', '2026-01-02')", (eid,))
        con.execute("INSERT INTO links VALUES (?, 1, 'same', '', '2026-01-02')", (eid,))
    assert statuses(con, [1], {}) == {1: "invalid"}
    assert _sources(con, 1, "S1") == 2


def test_prepare_failure_is_soft(root, monkeypatch):
    """不丟例外，也不留下舊背景。"""
    def broken(prompt: str, stage: str) -> dict:
        raise RuntimeError("額度用完")
    (root / "data" / "curated" / "2026-01-03.json").write_text(json.dumps({"items": [{"uid": "e" * 16, "title": "t", "summary": "s"}]}), encoding="utf-8")
    (root / "data" / "kg").mkdir(parents=True)
    (root / "data" / "kg" / "2026-01-03.json").write_text("{}", encoding="utf-8")
    prepare("2026-01-03", "token", root, broken)
    assert not (root / "data" / "kg" / "2026-01-03.json").exists()
