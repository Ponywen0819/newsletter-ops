import json

import psycopg
import pytest

from newsletter_shared import kg_store
from newsletter_shared.kg_store import connect, dump, load, norm, open_readonly, sources, sources_many, statuses


def test_norm():
    assert norm("Open AI，Inc.") == norm("openai inc") == "openaiinc"
    assert norm("台積電 (TSMC)") == "台積電tsmc"


def test_unconfigured(monkeypatch):
    """沒設 NEWSLETTER_DATABASE_URL：讀的一方當作還沒有 KG，寫的一方明確報錯。"""
    monkeypatch.delenv(kg_store.ENV, raising=False)
    assert open_readonly() is None
    with pytest.raises(RuntimeError):
        connect()


@pytest.fixture
def ro(pg):
    """a 被 b 矛盾、和 c 是同一件事。"""
    assert open_readonly() is None  # 還沒建過表
    con = connect()
    with con.transaction():
        cur = con.cursor()
        cur.executemany("INSERT INTO items VALUES (%s, '2026-01-01', 't', 'u', %s, 1)", [("a", "S1"), ("b", "S2"), ("c", "S3")])
        cur.executemany("INSERT INTO events(id, uid, date, summary, quote) VALUES (%s, %s, '2026-01-01', 's', 'q')",
                        [(1, "a"), (2, "b"), (3, "c")])
        cur.executemany("INSERT INTO mentions(event, key, name, type) VALUES (%s, %s, %s, 'org')",
                        [(1, "openai", "OpenAI"), (2, "openai", "OpenAI")])
        con.execute("INSERT INTO links VALUES (2, 1, 'contradict', 'q', '2026-01-01')")
        con.execute("INSERT INTO links VALUES (3, 1, 'same', '', '2026-01-01')")
    con.close()
    con = open_readonly()
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
    assert sources(ro, 2, "S2") == 1  # contradict 不算同一件事


def test_sources_many(ro):
    """批次版與逐筆的 sources() 一致：same 兩端都算到，自己的來源只算一次。"""
    own = {1: "S1", 2: "S2", 3: "S3"}
    assert sources_many(ro, own) == {eid: sources(ro, eid, src) for eid, src in own.items()} == {1: 2, 2: 1, 3: 2}
    assert sources_many(ro, {1: "S3"}) == {1: 1}  # 連到的來源和自己同一家
    assert sources_many(ro, {}) == {}


def test_statuses_trusted_overrides(ro):
    """使用者標可信的優先於矛盾。"""
    assert statuses(ro, [1, 2, 3], {"a": "trusted"}) == {1: "active", 2: "active", 3: "active"}
    assert statuses(ro, [1, 99], {}) == {1: "disputed", 99: "active"}  # 不存在的 id 當 active


def test_readonly(ro):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        ro.execute("DELETE FROM items")


def test_connect_autocommit_and_idempotent(pg):
    """寫入的連線是 autocommit：沒包交易的寫入別的連線馬上看得到。重複 connect 不會因為表已存在而失敗。"""
    con = connect()
    con.execute("INSERT INTO items(uid) VALUES ('x')")
    connect().close()
    with open_readonly() as other:
        assert other.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    con.close()


def test_dump_load_round_trip(ro):
    """devtools 的快照與 SQLite 匯入用：dump 是可 JSON 化的 {表: [列]}，load 整批取代，id 序號接在最大值後面。"""
    data = dump(ro)
    assert set(data) == {"items", "events", "mentions", "links"}
    assert len(data["events"]) == 3 and len(data["links"]) == 2
    data = json.loads(json.dumps(data))
    con = connect()
    try:
        con.execute("INSERT INTO items(uid) VALUES ('stale')")  # load 前就有的資料要被清掉
        load(con, data)
        assert dump(con) == data
        assert con.execute("INSERT INTO events(uid) VALUES ('d') RETURNING id").fetchone()[0] == 4
        assert con.execute("INSERT INTO mentions(event, key) VALUES (4, 'k') RETURNING id").fetchone()[0] > max(
            r[0] for r in data["mentions"])
    finally:
        con.close()
