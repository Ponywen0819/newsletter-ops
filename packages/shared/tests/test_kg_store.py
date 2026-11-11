import sqlite3

import pytest

from newsletter_shared.kg_store import SCHEMA, norm, open_readonly, sources, statuses


def test_norm():
    assert norm("Open AI，Inc.") == norm("openai inc") == "openaiinc"
    assert norm("台積電 (TSMC)") == "台積電tsmc"


@pytest.fixture
def ro(tmp_path):
    """a 被 b 矛盾、和 c 是同一件事。路徑有空白與中文，確認 URI 有編碼。"""
    db = tmp_path / "目錄 有空白" / "kg.sqlite"
    assert open_readonly(db) is None  # 還沒建過
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    with con:
        con.executemany("INSERT INTO items VALUES (?, '2026-01-01', 't', 'u', ?, 1)", [("a", "S1"), ("b", "S2"), ("c", "S3")])
        con.executemany("INSERT INTO events(id, uid, date, summary, quote) VALUES (?, ?, '2026-01-01', 's', 'q')",
                        [(1, "a"), (2, "b"), (3, "c")])
        con.execute("INSERT INTO links VALUES (2, 1, 'contradict', 'q', '2026-01-01')")
        con.execute("INSERT INTO links VALUES (3, 1, 'same', '', '2026-01-01')")
    con.close()
    con = open_readonly(db)
    assert con is not None
    yield con
    con.close()


def test_statuses(ro):
    assert statuses(ro, [1, 2], {}) == {1: "disputed", 2: "active"}
    assert statuses(ro, [1], {"b": "wrong"}) == {1: "active"}  # 矛盾的那則被標錯：不算
    assert statuses(ro, [1], {"a": "wrong", "c": "trusted"}) == {1: "invalid"}
    assert statuses(ro, [], {}) == {}


def test_sources(ro):
    assert sources(ro, 1, "S1") == 2


def test_readonly(ro):
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM items")
