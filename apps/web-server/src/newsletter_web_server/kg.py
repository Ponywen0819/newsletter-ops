"""知識圖譜的唯讀檢視：讀 state/kg.sqlite（writer 的 newsletter-kg 寫入），給網頁畫脈絡圖。

只用唯讀連線，不建表、不寫入；事件狀態用 shared 的 kg_store.statuses 算，與 writer 寫背景時同一套規則，
並套用 state/kg_flags.jsonl（使用者在網頁上標的「事實有誤」）。
摘要、引句、標題都是從外部網頁萃取的文字，前端只當純文字顯示。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from newsletter_shared import kg_flags, kg_store

from .errors import ApiError, NotFound

MAX_EVENTS = 150        # 一張圖最多幾個事件；超過就截斷並標 truncated
MAX_UIDS = 10           # /api/kg/item/<uid,uid,…> 一次最多幾則（併了多篇的新聞）
DEPTHS = (1, 2)
DEFAULT_DAYS, MAX_DAYS = 90, 365
TOP_DAYS, TOP_LIMIT, SEARCH_LIMIT = 30, 30, 50
MAX_QUERY, MAX_KEY = 100, 200
RELATED_LIMIT = 12


def _int(value: str, allowed: Callable[[int], bool], name: str) -> int:
    try:
        n = int(value)
    except ValueError:
        n = None
    if n is None or not allowed(n):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} 不合法")
    return n


def _marks(values) -> str:
    return ",".join("?" * len(values))


class Graph:
    def __init__(self, root: Path, today: Callable[[], str], report_of: Callable[[], dict[str, str]]):
        self.db = root / "state" / "kg.sqlite"
        self.flags_path = root / "state" / "kg_flags.jsonl"
        self.today = today
        self.report_of = report_of  # uid → 收錄它的最新一份晨報日期

    def _open(self) -> sqlite3.Connection:
        con = kg_store.open_readonly(self.db)
        if con is None:
            raise NotFound("kg")
        return con

    def _graph(self, con: sqlite3.Connection, ids: list[int], flags: dict[str, str]) -> dict:
        """事件（含實體、狀態、來源數、所在晨報）與它們之間的連結；舊到新。"""
        if not ids:
            return {"events": [], "links": [], "flags": {}}
        marks = _marks(ids)
        entities: dict[int, list[dict]] = {}
        for eid, key, name, type_ in con.execute(
                f"SELECT event, key, name, type FROM mentions WHERE event IN ({marks}) ORDER BY rowid", ids):
            entities.setdefault(eid, []).append({"key": key, "name": name, "type": type_})
        status = kg_store.statuses(con, ids, flags)
        reports = self.report_of()
        events = []
        for eid, uid, day, summary, quote, title, url, source in con.execute(
                f"""SELECT e.id, e.uid, e.date, e.summary, e.quote, i.title, i.url, i.source
                    FROM events e JOIN items i ON i.uid=e.uid WHERE e.id IN ({marks}) ORDER BY e.date, e.id""", ids):
            events.append({"id": eid, "uid": uid, "date": day, "summary": summary, "quote": quote,
                           "title": title, "url": url, "source": source, "status": status[eid],
                           "sources": kg_store.sources(con, eid, source), "report": reports.get(uid),
                           "entities": entities.get(eid, [])})
        links = [{"src": s, "dst": d, "kind": k, "quote": q or "", "date": day} for s, d, k, q, day in con.execute(
            f"""SELECT DISTINCT src, dst, kind, quote, date FROM links
                WHERE src IN ({marks}) AND dst IN ({marks}) ORDER BY date, src, dst""", ids + ids)]
        uids = {e["uid"] for e in events}
        return {"events": events, "links": links,
                "flags": {u: f for u, f in flags.items() if u in uids and f == "wrong"}}

    def item(self, uids: list[str], depth: str) -> dict:
        """以這幾則（併了多篇的新聞）的事件為中心，沿連結往外 depth 層。"""
        uids = list(dict.fromkeys(uids))
        if len(uids) > MAX_UIDS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"一次最多 {MAX_UIDS} 則")
        hops = _int(depth, lambda n: n in DEPTHS, "depth")
        flags = kg_flags.read(self.flags_path)
        reports = self.report_of()
        with closing(self._open()) as con:
            items = [{"uid": uid, "date": day, "title": title, "url": url, "source": source, "report": reports.get(uid)}
                     for uid, day, title, url, source in con.execute(
                         f"SELECT uid, date, title, url, source FROM items WHERE uid IN ({_marks(uids)})", uids)]
            if not items:
                raise NotFound(",".join(uids))
            seeds = [r[0] for r in con.execute(
                f"SELECT id FROM events WHERE uid IN ({_marks(uids)}) ORDER BY id", uids)][:MAX_EVENTS]
            seen, frontier, truncated = set(seeds), list(seeds), False
            for _ in range(hops):
                if not frontier:
                    break
                marks = _marks(frontier)
                near = sorted({n for (n,) in con.execute(
                    f"""SELECT dst FROM links WHERE src IN ({marks}) UNION SELECT src FROM links WHERE dst IN ({marks})""",
                    frontier + frontier)} - seen, reverse=True)  # id 大的較新，截斷時留新的
                room = MAX_EVENTS - len(seen)
                if len(near) > room:
                    truncated, near = True, near[:room]
                seen.update(near)
                frontier = near
            graph = self._graph(con, sorted(seen), flags)
        return {"uids": uids, "items": items, "depth": hops, "seeds": seeds, **graph, "truncated": truncated}

    def entity(self, key: str, days: str) -> dict:
        """提到這個實體的事件，近 days 天，新的優先（最多 MAX_EVENTS 個）；附上常一起出現的實體。"""
        if not key or len(key) > MAX_KEY or key != kg_store.norm(key):
            raise NotFound(key)
        span = _int(days, lambda n: 1 <= n <= MAX_DAYS, "days")
        since = (date.fromisoformat(self.today()) - timedelta(days=span)).isoformat()
        flags = kg_flags.read(self.flags_path)
        with closing(self._open()) as con:
            named = con.execute("""SELECT name, type, COUNT(*) AS n FROM mentions WHERE key=?
                                   GROUP BY name, type ORDER BY n DESC, name LIMIT 1""", (key,)).fetchone()
            if named is None:
                raise NotFound(key)
            ids = [r[0] for r in con.execute(
                """SELECT DISTINCT e.id FROM events e JOIN mentions m ON m.event=e.id
                   WHERE m.key=? AND e.date>=? ORDER BY e.date DESC, e.id DESC LIMIT ?""", (key, since, MAX_EVENTS + 1))]
            truncated, ids = len(ids) > MAX_EVENTS, ids[:MAX_EVENTS]
            total = con.execute("SELECT COUNT(DISTINCT event) FROM mentions WHERE key=?", (key,)).fetchone()[0]
            related = [{"key": k, "name": n, "type": t, "count": c} for k, n, t, c in con.execute(
                f"""SELECT key, MIN(name), MIN(type), COUNT(DISTINCT event) AS c FROM mentions
                    WHERE event IN ({_marks(ids)}) AND key != ? GROUP BY key ORDER BY c DESC, key LIMIT ?""",
                [*ids, key, RELATED_LIMIT])] if ids else []
            graph = self._graph(con, sorted(ids), flags)
        return {"entity": {"key": key, "name": named[0], "type": named[1], "total": total},
                "days": span, "since": since, "related": related, **graph, "truncated": truncated}

    def entities(self, q: str) -> dict:
        """q 空白＝近 TOP_DAYS 天最常出現的實體；否則找 key 含 norm(q) 的實體（不限日期）。事件多的在前。"""
        q = q.strip()
        if len(q) > MAX_QUERY:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"搜尋字串最多 {MAX_QUERY} 字")
        key = kg_store.norm(q)
        since = None if q else (date.fromisoformat(self.today()) - timedelta(days=TOP_DAYS)).isoformat()
        con = kg_store.open_readonly(self.db)
        if con is None:
            return {"q": q, "available": False, "since": since, "entities": []}
        if q and not key:  # 只有標點：沒有能比的
            con.close()
            return {"q": q, "available": True, "since": None, "entities": []}
        where, args, limit = ("instr(m.key, ?) > 0", [key], SEARCH_LIMIT) if q else ("e.date >= ?", [since], TOP_LIMIT)
        with closing(con):
            rows = con.execute(
                f"""SELECT m.key, MIN(m.name), MIN(m.type), COUNT(DISTINCT m.event) AS n, MAX(e.date) AS last
                    FROM mentions m JOIN events e ON e.id=m.event WHERE {where}
                    GROUP BY m.key ORDER BY n DESC, last DESC, m.key LIMIT ?""", [*args, limit]).fetchall()
        return {"q": q, "available": True, "since": since,
                "entities": [{"key": k, "name": n, "type": t, "count": c, "last": last} for k, n, t, c, last in rows]}
