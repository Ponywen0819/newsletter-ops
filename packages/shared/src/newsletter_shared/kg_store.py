"""知識圖譜（state/kg.sqlite）的資料表與判讀規則，writer（寫入、產生背景）與 web-server（唯讀的脈絡圖）共用。

寫入只在 writer 的 newsletter_writer.kg；這裡只放兩邊都要、而且必須一致的東西：
資料表定義、實體名稱的正規化（mentions.key）、事件狀態（active／disputed／invalid）與來源數的算法。
"""
from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(uid TEXT PRIMARY KEY, date TEXT, title TEXT, url TEXT, source TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, uid TEXT, date TEXT, summary TEXT, quote TEXT,
                                  related INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS mentions(event INTEGER, key TEXT, name TEXT, type TEXT);
CREATE TABLE IF NOT EXISTS links(src INTEGER, dst INTEGER, kind TEXT, quote TEXT, date TEXT);
CREATE INDEX IF NOT EXISTS mentions_key ON mentions(key);
CREATE INDEX IF NOT EXISTS events_uid ON events(uid);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst);
"""

TYPES = {"org", "person", "product", "place", "other"}
KINDS = {"same", "follow", "contradict", "retract"}
STATUSES = ("active", "disputed", "invalid")


def norm(text: str) -> str:
    """比對用：全半形、大小寫、空白與標點都不算。也是實體的 key（mentions.key）。"""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def open_readonly(path: Path, timeout: float = 5.0) -> sqlite3.Connection | None:
    """唯讀開啟；檔案不存在（還沒建過 KG）是 None。writer 寫入時最多等 timeout 秒。"""
    if not path.is_file():
        return None
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)


def statuses(con: sqlite3.Connection, ids: list[int], flags: dict[str, str]) -> dict[int, str]:
    """active／disputed／invalid。使用者的判定優先；被標 wrong 的則對別則的矛盾、撤回不算。
    一個來源矛盾＝有爭議；明確撤回，或兩個以上來源矛盾＝失效。"""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out = {eid: "active" for eid in ids}
    for eid, uid in con.execute(f"SELECT id, uid FROM events WHERE id IN ({marks})", ids):
        if flags.get(uid) == "wrong":
            out[eid] = "invalid"
    against: dict[int, dict[str, set]] = {}
    for dst, kind, src_uid, source in con.execute(
            f"""SELECT l.dst, l.kind, e.uid, i.source FROM links l JOIN events e ON e.id=l.src JOIN items i ON i.uid=e.uid
                WHERE l.dst IN ({marks}) AND l.kind IN ('contradict', 'retract')""", ids):
        if flags.get(src_uid) != "wrong":
            against.setdefault(dst, {}).setdefault(kind, set()).add(source)
    for eid, kinds in against.items():
        if out[eid] == "invalid":
            continue
        if kinds.get("retract") or len(kinds.get("contradict", ())) >= 2:
            out[eid] = "invalid"
        elif kinds.get("contradict"):
            out[eid] = "disputed"
    trusted = [eid for eid, uid in con.execute(f"SELECT id, uid FROM events WHERE id IN ({marks})", ids)
               if flags.get(uid) == "trusted"]
    out.update({eid: "active" for eid in trusted})
    return out


def sources(con: sqlite3.Connection, eid: int, own: str) -> int:
    """報導這個事件的來源數：自己的來源加上 same 連到的其他來源。"""
    rows = con.execute("""SELECT i.source FROM links l JOIN events e ON e.id = CASE WHEN l.src=? THEN l.dst ELSE l.src END
                          JOIN items i ON i.uid=e.uid WHERE l.kind='same' AND (l.src=? OR l.dst=?)""", (eid, eid, eid))
    return len({own, *(r[0] for r in rows)})

