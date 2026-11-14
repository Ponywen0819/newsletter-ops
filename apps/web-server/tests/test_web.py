"""真的 HTTP 伺服器＋暫存目錄，不呼叫 Claude。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from newsletter_shared import auth_health, auth_store, feedback, kg_flags, kg_store

from helpers import served

from newsletter_web_server.auth import STALE_AFTER
from newsletter_web_server.server import IMMUTABLE, MAX_BODY, Site


UID_A, UID_B, UID_C = "0123456789abcdef", "1111111111111111", "2222222222222222"
MD = f"""# 每日晨間簡報 2026-09-28

<!-- subject: 測試頭條 -->

> **今日頭條：** 某事發生，見 [來源](https://a.example/x)。

## 科技與 AI

**[重點 <標題>](https://a.example/1)**

- 發生什麼：A
  - 細節
<!-- mark:    uid={UID_A} -->

## 其餘收錄

- **[其他](https://a.example/2)** — 半句
<!-- mark:    uid={UID_B} -->
- **[其他二](https://a.example/3)** — 第二則
<!-- mark:    uid={UID_C} -->
"""
SHELL = '<!doctype html><div id="root"></div><script type="module" src="/assets/app-abc123.js"></script>'


@pytest.fixture
def root(tmp_path):
    root = tmp_path
    (root / "reports").mkdir()
    (root / "data" / "curated").mkdir(parents=True)
    (root / "reports" / "2026-09-28.md").write_text(MD, encoding="utf-8")
    (root / "reports" / "2026-09-27.md").write_text(MD.replace("2026-09-28", "2026-09-27"), encoding="utf-8")
    (root / "reports" / "notes.md").write_text("不是日期檔名，要被略過", encoding="utf-8")
    (root / "data" / "curated" / "2026-09-28.json").write_text(json.dumps(
        {"items": [{"uid": UID_A, "title": "重點", "source": "S1", "topic": "ai-industry",
                    "matched_keywords": ["llm"]}]}, ensure_ascii=False), encoding="utf-8")
    # 前端的建置結果（假的）；dist 之外放一個檔案，確認拿不到
    (root / "apps" / "web-ui" / "dist" / "assets").mkdir(parents=True)
    (root / "apps" / "web-ui" / "dist" / "index.html").write_text(SHELL, encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "assets" / "app-abc123.css").write_text("body{}", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "manifest.webmanifest").write_text('{"name":"x"}', encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "sw.js").write_text("self.skipWaiting()", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "icons").mkdir()
    (root / "apps" / "web-ui" / "dist" / "icons" / "icon-192.png").write_text("png", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist" / "build.py").write_text("print('不該被提供')", encoding="utf-8")
    for d in (root / "apps" / "web-ui", root / "apps", root):
        (d / "secret.txt").write_text("dist 之外", encoding="utf-8")
    return root


@pytest.fixture
def site(root):
    return Site(root, today=lambda: "2026-09-28")


@pytest.fixture
def req(site):
    with served(site) as r:
        yield r


@pytest.fixture
def shell():
    return SHELL


@pytest.fixture
def uid_a():
    return UID_A


@pytest.fixture
def uids():
    return UID_A, UID_B, UID_C


def test_no_frontend(tmp_path):
    # 還沒建置前端：頁面回 503 並說明怎麼建置，API 照常
    bare = tmp_path / "bare"
    (bare / "reports").mkdir(parents=True)
    with served(Site(bare, today=lambda: "2026-09-28")) as req:
        status, _, body = req("GET", "/")
        assert status == 503 and "npm run build" in body
        status, _, body = req("GET", "/api/today")
        assert status == 200 and json.loads(body) == {"date": "2026-09-28", "report": None, "marks": {}, "flags": {}, "latest": None,
                                                 "run": None}
        assert json.loads(req("GET", "/api/reports")[2]) == {"reports": []}


def test_timeout(site: Site) -> None:
    """只送一半的請求：逾時後伺服器關掉連線，不會一直佔著 thread。"""
    import socket
    import time
    from unittest import mock

    from newsletter_web_server.server import Handler, make_server
    with mock.patch.object(Handler, "timeout", 0.5):
        server = make_server(site, "127.0.0.1", 0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with socket.create_connection(server.server_address, timeout=5) as sock:
                sock.sendall(b"GET /api/session HTTP/1.1\r\nHost: x\r\n")  # 標頭沒送完
                t0 = time.monotonic()
                assert sock.recv(1024) == b"" and time.monotonic() - t0 < 4  # 被關掉，不是等到 client 自己逾時
        finally:
            server.shutdown()
            server.server_close()


U1, U2, U3 = "3" * 16, "4" * 16, "5" * 16


def _get(req, path: str) -> tuple[int, dict]:
    status, _, body = req("GET", path)
    return status, json.loads(body)


@pytest.fixture
def kg_db(root: Path) -> Path:
    """UID_A（在 09-28、09-27 兩份晨報裡）完成收購 → 跟進 U1 的否認 → U1 與 U2 的宣布矛盾；U3 是很久以前的。"""
    db = root / "state" / "kg.sqlite"
    db.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(kg_store.SCHEMA)
    with con:
        con.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, 1)", [
            (UID_A, "2026-09-28", "Acme 完成", "https://s1/a", "S1"), (U1, "2026-09-20", "Beta 否認", "https://s2/b", "S2"),
            (U2, "2026-09-10", "Acme 宣布", "https://s3/c", "S3"), (U3, "2026-01-01", "Acme 成立", "https://s4/d", "S4")])
        con.executemany("INSERT INTO events(id, uid, date, summary, quote) VALUES (?, ?, ?, ?, ?)", [
            (1, U2, "2026-09-10", "Acme 宣布收購 Beta", "q1"), (2, U1, "2026-09-20", "Beta 否認", "q2"),
            (3, UID_A, "2026-09-28", "Acme 完成收購", "q3"), (4, U3, "2026-01-01", "Acme 成立", "q4")])
        con.executemany("INSERT INTO mentions VALUES (?, ?, ?, ?)", [
            (1, "acme", "Acme", "org"), (1, "beta", "Beta", "org"), (2, "beta", "Beta", "org"), (2, "acme", "ACME", "org"),
            (3, "acme", "Acme", "org"), (3, "台積電", "台積電", "org"), (4, "acme", "Acme", "org")])
        con.executemany("INSERT INTO links VALUES (?, ?, ?, ?, ?)", [
            (2, 1, "contradict", "not in talks", "2026-09-20"), (3, 2, "follow", "", "2026-09-28"),
            (3, 2, "follow", "", "2026-09-28")])  # 重複的連結只回一次
    con.close()
    return db


def test_kg_missing(req) -> None:
    """還沒建過 KG：總覽說沒有，個別的 404。"""
    assert _get(req, "/api/kg/entities") == (200, {"q": "", "available": False, "since": "2026-08-29", "entities": []})
    assert _get(req, f"/api/kg/item/{UID_A}")[0] == 404
    assert _get(req, "/api/kg/entity/acme")[0] == 404


def test_kg_item(req, root: Path, kg_db: Path) -> None:
    status, one = _get(req, f"/api/kg/item/{UID_A}")
    assert status == 200 and one["depth"] == 1 and one["seeds"] == [3], one
    assert [e["id"] for e in one["events"]] == [2, 3]  # 舊到新
    assert one["links"] == [{"src": 3, "dst": 2, "kind": "follow", "quote": "", "date": "2026-09-28"}]
    assert one["items"] == [{"uid": UID_A, "date": "2026-09-28", "title": "Acme 完成", "url": "https://s1/a", "source": "S1",
                             "report": "2026-09-28"}]
    ev3 = one["events"][1]
    assert ev3["report"] == "2026-09-28" and ev3["status"] == "active" and ev3["sources"] == 1
    assert ev3["entities"] == [{"key": "acme", "name": "Acme", "type": "org"}, {"key": "台積電", "name": "台積電", "type": "org"}]
    assert one["events"][0]["report"] is None  # 沒寫進晨報的則

    two = _get(req, f"/api/kg/item/{UID_A},{U1},{UID_A}?depth=2")[1]
    assert two["uids"] == [UID_A, U1] and [e["id"] for e in two["events"]] == [1, 2, 3] and len(two["links"]) == 2
    assert {e["id"]: e["status"] for e in two["events"]} == {1: "disputed", 2: "active", 3: "active"}
    assert two["flags"] == {}


def test_kg_flags_change_status(req, root: Path, kg_db: Path) -> None:
    """與 writer 同一套規則：否認的那則被標錯，它的矛盾就不算。"""
    kg_flags.append(U1, "wrong", root / "state" / "kg_flags.jsonl")
    two = _get(req, f"/api/kg/item/{UID_A}?depth=2")[1]
    assert {e["id"]: e["status"] for e in two["events"]} == {1: "active", 2: "invalid", 3: "active"}
    assert two["flags"] == {U1: "wrong"}
    # 確認可信也帶回去（知識圖譜頁要顯示按鈕的狀態）；同一則以最後一筆為準
    kg_flags.append(U1, "trusted", root / "state" / "kg_flags.jsonl")
    assert _get(req, f"/api/kg/item/{UID_A}?depth=2")[1]["flags"] == {U1: "trusted"}


@pytest.mark.parametrize("path", [
    f"/api/kg/item/{UID_A}?depth=3", f"/api/kg/item/{UID_A}?depth=x",
    "/api/kg/item/" + ",".join([UID_A] * 2 + [f"{i:016x}" for i in range(10)]),  # 去重後還是超過 10 則
    "/api/kg/entity/acme?days=0", "/api/kg/entity/acme?days=366", "/api/kg/entities?q=" + "a" * 101,
])
def test_kg_bad_request(req, kg_db: Path, path: str) -> None:
    assert _get(req, path)[0] == 400


@pytest.mark.parametrize("path", [
    "/api/kg/item/" + "6" * 16, "/api/kg/entity/Acme",  # 不是正規化過的 key
    "/api/kg/entity/nobody", "/api/kg/entity/a%2Fb",
])
def test_kg_not_found(req, kg_db: Path, path: str) -> None:
    assert _get(req, path)[0] == 404


def test_kg_entity(req, kg_db: Path) -> None:
    status, acme = _get(req, "/api/kg/entity/acme")
    assert status == 200 and acme["days"] == 90 and acme["since"] == "2026-06-30", acme
    assert acme["entity"] == {"key": "acme", "name": "Acme", "type": "org", "total": 4}  # 最常見的寫法
    assert [e["id"] for e in acme["events"]] == [1, 2, 3] and len(acme["links"]) == 2 and not acme["truncated"]
    assert [(r["key"], r["count"]) for r in acme["related"]] == [("beta", 2), ("台積電", 1)]
    assert [e["id"] for e in _get(req, "/api/kg/entity/acme?days=365")[1]["events"]] == [4, 1, 2, 3]
    tsmc = _get(req, "/api/kg/entity/" + quote("台積電"))[1]
    assert tsmc["entity"]["name"] == "台積電" and [e["id"] for e in tsmc["events"]] == [3]


def test_kg_entities(req, kg_db: Path) -> None:
    top = _get(req, "/api/kg/entities")[1]
    assert top["available"] and [(e["key"], e["count"]) for e in top["entities"]] == [("acme", 3), ("beta", 2), ("台積電", 1)]
    assert top["entities"][0]["last"] == "2026-09-28"
    found = _get(req, "/api/kg/entities?q=" + quote(" ACME "))[1]
    assert found["q"] == "ACME" and found["since"] is None and [(e["key"], e["count"]) for e in found["entities"]] == [("acme", 4)]
    assert _get(req, "/api/kg/entities?q=" + quote("，"))[1]["entities"] == []


def test_kg_pages_and_readonly(req, root: Path, kg_db: Path) -> None:
    """前端路由回 index.html；KG 不進 ETag／離線副本；網頁讀過之後資料庫一個字都沒改。"""
    for page in ("/kg", f"/kg/item/{UID_A}", f"/kg/item/{UID_A},{U1}", "/kg/entity/" + quote("台積電")):
        assert req("GET", page)[0] == 200, page
    assert req("GET", "/kg/item/xyz")[0] == 404
    assert "etag" not in req("GET", f"/api/kg/item/{UID_A}")[1]
    before = kg_db.read_bytes()
    _get(req, f"/api/kg/item/{UID_A}?depth=2")
    assert kg_db.read_bytes() == before and not (root / "state" / "kg.sqlite-journal").exists()


def test_etag(req, uid_a: str) -> None:
    """離線副本的條件請求：沒變回 304（沒有 body），標記或晨報變了 ETag 跟著變。"""
    path = "/api/reports/2026-09-28"
    status, headers, _ = req("GET", path)
    etag = headers["etag"]
    assert status == 200 and etag.startswith('"') and headers["cache-control"] == "no-store", headers
    for sent in (etag, f"W/{etag}", f'"x", {etag}', "*"):  # Cloudflare 可能改成弱 ETag
        status, headers, again = req("GET", path, {"If-None-Match": sent})
        assert status == 304 and again == "" and headers["etag"] == etag, (sent, status)
    assert req("GET", path, {"If-None-Match": '"nope"'})[0] == 200
    for p in (path, "/", "/api/nope"):  # HEAD：狀態與標頭同 GET，沒有 body
        got, head = req("GET", p), req("HEAD", p)
        assert head[0] == got[0] and head[2] == "" and head[1]["content-length"] == got[1]["content-length"], (p, head)
    assert req("GET", "/api/reports")[1].get("etag") and req("GET", "/api/today")[1].get("etag")
    assert "etag" not in req("GET", "/api/session")[1]  # 只有同步的對象有
    for p in ("/", "/api/session", "/api/nope", "/assets/app-abc123.js"):
        assert req("GET", p)[1]["referrer-policy"] == "no-referrer", p
    req("POST", "/api/feedback", {"Content-Type": "application/json"}, {"uid": uid_a, "mark": "+"})
    status, headers, _ = req("GET", path, {"If-None-Match": etag})
    assert status == 200 and headers["etag"] != etag  # 標記變了
    req("POST", "/api/feedback", {"Content-Type": "application/json"}, {"uid": uid_a, "mark": ""})
    assert req("GET", path, {"If-None-Match": etag})[0] == 304  # 改回去就和原本一樣

    # 事實有誤：寫 kg_flags.jsonl，報告的 payload 帶回 flags
    json_h = {"Content-Type": "application/json"}
    status, _, text = req("POST", "/api/kg-flag", json_h, {"uid": uid_a, "flag": "wrong"})
    assert status == 200 and json.loads(text) == {"uid": uid_a, "flag": "wrong"}, text
    assert json.loads(req("GET", path)[2])["flags"] == {uid_a: "wrong"}
    # 確認可信（知識圖譜頁用）：蓋掉有誤；晨報的 payload 只帶有誤，可信不帶
    status, _, text = req("POST", "/api/kg-flag", json_h, {"uid": uid_a, "flag": "trusted"})
    assert status == 200 and json.loads(text)["flag"] == "trusted"
    assert json.loads(req("GET", path)[2])["flags"] == {}
    assert req("POST", "/api/kg-flag", json_h, {"uid": uid_a, "flag": "maybe"})[0] == 400
    assert req("POST", "/api/kg-flag", json_h, {"uid": "e" * 16, "flag": "wrong"})[0] == 404
    assert req("POST", "/api/kg-flag", json_h, {"uid": uid_a})[0] == 400
    req("POST", "/api/kg-flag", json_h, {"uid": uid_a, "flag": ""})
    assert json.loads(req("GET", path)[2])["flags"] == {}


def test_reports(req, site: Site, root: Path, uid_a: str) -> None:
    def get(path: str, headers=None) -> tuple[int, dict]:
        status, _, text = req("GET", path, headers)
        return status, json.loads(text)

    # 晨報：回結構，不是 HTML；巢狀子項目不帶 mark，mark 掛在最外層的項目上（與 email 版型一致）
    status, data = get("/api/reports/2026-09-28")
    assert status == 200 and data["date"] == "2026-09-28" and data["marks"] == {}, data
    assert (data["prev"], data["next"]) == ("2026-09-27", None), data  # 前一份＝更舊的，下一份＝更新的
    older = get("/api/reports/2026-09-27")[1]
    assert (older["prev"], older["next"]) == (None, "2026-09-28"), older
    report = data["report"]
    assert report["headline"] == "某事發生，見 來源。" and report["subject"] == "測試頭條"
    assert [b["type"] for b in report["blocks"]] == ["title", "callout", "heading", "paragraph", "list",
                                                       "heading", "list"], report["blocks"]
    item = report["blocks"][4]["items"][0]
    assert item["uids"] == [uid_a] and item["children"][0]["inline"] == [{"type": "text", "text": "細節"}]
    assert "uids" not in item["children"][0]
    assert [s["url"] for s in report["sources"]] == ["https://a.example/x", "https://a.example/1",
                                                      "https://a.example/2", "https://a.example/3"]
    raw = req("GET", "/api/reports/2026-09-28")[2]
    assert "重點 <標題>" in raw and "mark:" not in raw and "<div" not in raw

    # 當日：有就給整份，沒有就給最新一份的日期與整份（fallback）
    assert get("/api/today") == (200, {**data, "latest": "2026-09-28", "run": None})
    site.reports.today = lambda: "2026-10-05"
    assert get("/api/today") == (200, {"date": "2026-10-05", "report": None, "marks": {}, "flags": {}, "latest": "2026-09-28",
                                       "fallback": data, "run": None})

    # 當天的執行紀錄（worker 寫的 last_run.json）：只給當天的，壞掉當沒有
    record = root / "state" / "last_run.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    rec = {"date": "2026-10-05", "trigger": "schedule", "started_at": "a", "finished_at": "b", "exit_code": 3,
           "step": "collect"}
    record.write_text(json.dumps(rec), encoding="utf-8")
    assert get("/api/today")[1]["run"] == {k: v for k, v in rec.items() if k != "date"}
    record.write_text(json.dumps({**rec, "date": "2026-10-04"}), encoding="utf-8")
    assert get("/api/today")[1]["run"] is None
    record.write_text("{", encoding="utf-8")
    assert get("/api/today")[1]["run"] is None
    record.unlink()
    site.reports.today = lambda: "2026-09-28"

    # 搜尋：不分大小寫、去掉語法與 mark、新到舊；空字串沒有結果；太長 400；超過上限截斷
    status, data = get("/api/search?q=%E7%B4%B0%E7%AF%80")  # 細節
    assert status == 200 and data["results"][0] == {"date": "2026-09-28", "text": "細節"}, data
    assert [r["date"] for r in data["results"]] == sorted((r["date"] for r in data["results"]), reverse=True)
    status, data = get("/api/search?q=" + "%E6%A8%99%E9%A1%8C")  # 標題
    assert status == 200 and data["results"] and all("mark:" not in r["text"] and "**" not in r["text"]
                                                      for r in data["results"]), data
    assert get("/api/search?q=")[1] == {"q": "", "results": [], "truncated": False}
    both = get("/api/search?q=%E7%B4%B0+%E7%AF%80")[1]["results"]  # 細 節：兩個詞都要有
    assert both and all("細" in r["text"] and "節" in r["text"] for r in both), both
    assert get("/api/search?q=%E7%B4%B0%E7%AF%80+zzzz")[1]["results"] == []
    assert get("/api/search")[1]["results"] == []
    assert get("/api/search?q=" + "x" * 101)[0] == 400
    import newsletter_web_server.reports as reports_mod
    saved_limit, reports_mod.SEARCH_LIMIT = reports_mod.SEARCH_LIMIT, 1
    try:
        data = get("/api/search?q=a")[1]
        assert len(data["results"]) == 1 and data["truncated"], data
    finally:
        reports_mod.SEARCH_LIMIT = saved_limit

    # 歷史列表：新到舊、略過非日期檔名、顯示頭條
    status, data = get("/api/reports")
    assert status == 200 and data == {"reports": [{"date": "2026-09-28", "headline": "某事發生，見 來源。"},
                                                  {"date": "2026-09-27", "headline": "某事發生，見 來源。"}]}, data
    assert get("/api/reports/2026-01-01")[0] == 404 and get("/api/reports/..%2Fconfig")[0] == 404
    assert get("/api/nope") == (404, {"error": "not found"}) and get("/api/reports/")[0] == 200

    # heartbeat：200 {ok}、no-store、不寫入任何東西（檔案清單與 mtime 前後相同）、只有 GET
    def tree() -> dict[Path, int]:
        return {p: p.stat().st_mtime_ns for p in root.rglob("*")}
    before = tree()
    status, headers, text = req("GET", "/api/heartbeat")
    assert (status, json.loads(text), headers["cache-control"]) == (200, {"ok": True}, "no-store")
    assert req("POST", "/api/heartbeat", None, {})[0] == 404 and tree() == before

    # 格式不符的報告：單日回 500 與原因，列表照樣能列出（頭條抓不到就留空）
    (root / "reports" / "2026-09-26.md").write_text("# 壞掉的報告\n", encoding="utf-8")
    status, data = get("/api/reports/2026-09-26")
    assert status == 500 and "報告格式不符" in data["error"] and "頭條" in data["error"], data
    assert get("/api/reports")[1]["reports"][-1] == {"date": "2026-09-26", "headline": ""}
    (root / "reports" / "2026-09-26.md").unlink()


def test_static(req, shell: str) -> None:
    # 前端：路由一律回 index.html（含 CSP）；沒有行內腳本與樣式
    for path in ("/", "/reports", "/reports/", "/reports/2026-09-28", "/reports/2026-01-01", "/auth", "/stats",
                 "/feedback/0123456789abcdef", "/feedback/0123456789abcdef,1111111111111111"):
        status, headers, body = req("GET", path)
        assert status == 200 and body == shell, (path, status)
        assert headers["content-type"] == "text/html; charset=utf-8" and headers["cache-control"] == "no-cache"
        assert "default-src 'self'" in headers["content-security-policy"] and "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "<style" not in shell and "<script>" not in shell
    # 不認得的路徑：同一份 index.html，但狀態碼是 404（前端畫「找不到頁面」）
    for path in ("/nope", "/reports/abc", "/api", "/reports/2026-09-28/x", "/feedback/0123456789abcdef,"):
        status, _, body = req("GET", path)
        assert status == 404 and body == shell, (path, status)
    # 靜態檔：assets 帶 hash 長期快取，其他每次確認；類型照副檔名
    status, headers, body = req("GET", "/assets/app-abc123.js")
    assert status == 200 and body == "console.log(1)" and headers["content-type"] == "text/javascript; charset=utf-8"
    assert headers["cache-control"] == IMMUTABLE and headers["x-content-type-options"] == "nosniff"
    assert req("GET", "/assets/app-abc123.css")[1]["content-type"] == "text/css; charset=utf-8"
    status, headers, _ = req("GET", "/favicon.svg")
    assert status == 200 and headers["content-type"] == "image/svg+xml" and headers["cache-control"] == "no-cache"
    # PWA：manifest、service worker、圖示都要每次確認（sw.js 被長期快取就永遠換不了新版）
    for path, ctype in (("/manifest.webmanifest", "application/manifest+json"),
                        ("/sw.js", "text/javascript; charset=utf-8"), ("/icons/icon-192.png", "image/png")):
        status, headers, _ = req("GET", path)
        assert status == 200 and headers["content-type"] == ctype, (path, status, headers["content-type"])
        assert headers["cache-control"] == "no-cache" and headers["x-content-type-options"] == "nosniff", path
    # 擋掉：不存在的檔案給純文字 404（不是 HTML）、dist 之外、副檔名不在白名單、目錄
    for path in ("/assets/missing.js", "/../secret.txt", "/assets/../../secret.txt", "/assets/../../../secret.txt",
                 "/assets/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt", "/secret.txt", "/build.py", "/assets", "/assets/"):
        status, headers, body = req("GET", path)
        assert status == 404 and "dist 之外" not in body and "不該被提供" not in body, (path, status)
        if "." in path.rsplit("/", 1)[-1]:
            assert headers["content-type"].startswith("text/plain") and body == "not found", path


def test_feedback(req, site: Site, root: Path, shell: str, uids: tuple[str, str, str]) -> None:
    uid_a, uid_b, uid_c = uids
    def get(path: str, headers=None) -> tuple[int, dict]:
        status, _, text = req("GET", path, headers)
        return status, json.loads(text)

    def post(payload, ctype: str = "application/json") -> tuple[int, dict]:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        status, _, text = req("POST", "/api/feedback", {"Content-Type": ctype}, raw)
        return status, json.loads(text)

    def saved() -> dict[str, dict]:
        return feedback.read_feedback(root / "state" / "feedback.jsonl")

    def marks(date: str = "2026-09-28") -> dict[str, str]:
        return get(f"/api/reports/{date}")[1]["marks"]

    # 寫入：欄位沿用 feedback.py，並帶出 curated 的中繼資料
    assert post({"uid": uid_a, "mark": "+"}) == (200, {"uid": uid_a, "mark": "+"})
    row = saved()[uid_a]
    assert row["mark"] == "+" and row["report"] == "2026-09-28.md" and row["title"] == "重點"
    assert row["topic"] == "ai-industry" and row["matched_keywords"] == ["llm"]
    assert set(row) == set(feedback.build_row(uid_a, "+", "x.md", {})), row
    assert marks() == {uid_a: "+"}
    # curated 裡沒有的 uid 也能標，只是沒有中繼資料
    assert post({"uid": uid_b, "mark": "-"})[0] == 200
    assert saved()[uid_b]["mark"] == "-" and saved()[uid_b]["title"] == ""

    # 覆蓋：以最後一筆為準；檔案是 append，不是改寫
    assert post({"uid": uid_a, "mark": "-"})[0] == 200
    assert saved()[uid_a]["mark"] == "-"
    assert get("/api/today")[1]["marks"] == {uid_a: "-", uid_b: "-"}
    lines = (root / "state" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["mark"] for ln in lines if uid_a in ln] == ["+", "-"], lines

    # 取消：mark "" 讀回來是未標記
    assert post({"uid": uid_a, "mark": ""}) == (200, {"uid": uid_a, "mark": ""})
    assert saved()[uid_a]["mark"] == ""
    assert marks() == {uid_b: "-"}

    # feedback.py 收進來的 ++ / -- 在網頁上算同一級；只回這份報告裡出現的 uid
    site.reports.feedback_path.write_text(site.reports.feedback_path.read_text(encoding="utf-8").rstrip("\n"),
                                  encoding="utf-8")
    assert post({"uid": uid_c, "mark": "+"})[0] == 200
    assert all(ln.startswith("{") for ln in site.reports.feedback_path.read_text(encoding="utf-8").splitlines())
    with site.reports.feedback_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(feedback.build_row(uid_c, "++", "2026-09-28.md", {})) + "\n")
        fh.write(json.dumps(feedback.build_row("f" * 16, "+", "2026-08-01.md", {})) + "\n")
    assert marks() == {uid_b: "-", uid_c: "+"}

    # 統計：每個 uid 只算最後一筆、取消的不算；uid_c 最後是 ++、f… 是 +、uid_b 是 -
    stats = get("/api/feedback-stats")[1]
    assert stats["total"] == 3, stats
    assert stats["groups"]["topic"][0] == {"name": "未知", "pos": 2, "neg": 1}, stats

    # email 連結的確認頁：GET 只讀，絕不寫入；標題來自 curated，沒有就空字串；mark 是目前的標記
    before = site.reports.feedback_path.read_text(encoding="utf-8")
    assert get(f"/api/feedback/{uid_a}") == (200, {"uid": uid_a, "date": "2026-09-28", "title": "重點", "mark": ""})
    assert get(f"/api/feedback/{uid_b}")[1] == {"uid": uid_b, "date": "2026-09-28", "title": "", "mark": "-"}
    assert get(f"/api/feedback/{uid_c}")[1]["mark"] == "+"
    for path in (f"/api/feedback/{'e' * 16}", "/api/feedback/xyz", "/api/feedback/", "/api/feedback"):
        assert get(path)[0] == 404, path
    for v in ("%2B", "-", "+"):
        status, _, body = req("GET", f"/feedback/{uid_a}?v={v}")
        assert status == 200 and body == shell, (v, status)
    assert req("GET", f"/feedback/{'e' * 16}")[0] == 200
    assert req("GET", "/feedback/xyz")[0] == 404 and req("GET", "/feedback")[0] == 404
    assert site.reports.feedback_path.read_text(encoding="utf-8") == before

    # 擋掉不合法的請求，且不寫檔
    assert post({"uid": uid_a, "mark": "++"})[0] == 400
    assert post({"uid": "xyz", "mark": "+"})[0] == 400
    assert post({"uid": "e" * 16, "mark": "+"})[0] == 404
    assert post({"uid": uid_a})[0] == 400 and post([1, 2])[0] == 400
    assert post(b"not json")[0] == 400
    assert post({"uid": uid_a, "mark": "+"}, ctype="application/x-www-form-urlencoded")[0] == 415
    assert post(b"x" * (MAX_BODY + 1))[0] == 413
    assert req("POST", "/feedback", None, {"uid": uid_a, "mark": "+"})[0] == 404
    assert site.reports.feedback_path.read_text(encoding="utf-8") == before


SECRET = "sk-ant-oat01-" + "Kp4" * 14


@pytest.fixture
def t(tmp_path):
    secret = SECRET

    # --- 整個 HTTP 流程（假的 checker，不呼叫 Claude）---
    uid = "0123456789abcdef"
    checked: list[str] = []
    outcome = {"v": {"ok": True, "kind": "ok", "message": "驗證通過"}}

    def checker(token: str) -> dict:
        checked.append(token)
        return outcome["v"]

    seen: list[str] = []

    root = tmp_path
    (root / "reports").mkdir()
    (root / "reports" / "2026-09-28.md").write_text(
        f"# 每日晨間簡報 2026-09-28\n\n<!-- subject: x -->\n\n> **今日頭條：** y\n\n## 科技\n\n"
        f"**[t](https://a.example/1)**\n\n- a\n<!-- mark:    uid={uid} -->\n", encoding="utf-8")
    (root / "apps" / "web-ui" / "dist").mkdir(parents=True)
    (root / "apps" / "web-ui" / "dist" / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    site = Site(root, today=lambda: "2026-09-28", checker=checker)

    with served(site) as raw_req:
        def req(method, path, headers=None, body=None):
            status, _, text = raw_req(method, path, headers, body)
            seen.append(text)
            return status, text

        def post(path, body, headers=None):
            status, text = req("POST", path, headers, body)
            return status, json.loads(text)

        def get(path, headers=None):
            status, text = req("GET", path, headers)
            return status, json.loads(text)

        missing = {"state": "missing", "kind": "missing", "checked_at": None}
        other = "sk-ant-oat01-" + "Zz1" * 14
        t = SimpleNamespace(site=site, req=req, get=get, post=post, secret=secret, other=other,
                            checked=checked, outcome=outcome, uid=uid, missing=missing, root=root)
        yield t

        # 完整 token 不能出現在任何回應裡
        assert not any(secret in text or other in text for text in seen)


def test_auth_access(t) -> None:
    """web 不做身分判斷（交給 nginx）：/auth、/api/auth* 照常提供，session 一律說可以管理。"""
    req, get, post, uid, missing = t.req, t.get, t.post, t.uid, t.missing
    assert get("/api/session") == (200, {"local": True})
    assert get("/api/auth") == (200, {"state": "missing"})
    assert req("GET", "/auth")[0] == 200
    assert get("/api/auth-health") == (200, missing)
    assert post("/api/auth/revoke", {})[0] == 200
    # 一般功能照常：讀報告、按 👍／👎
    assert get("/api/today")[0] == 200 and get("/api/heartbeat") == (200, {"ok": True})
    assert post("/api/feedback", {"uid": uid, "mark": "+"})[0] == 200
    assert uid in (t.root / "state" / "feedback.jsonl").read_text(encoding="utf-8")


def test_auth_token(t) -> None:
    import stat

    site, req, get, post, secret, other, checked, outcome = t.site, t.req, t.get, t.post, t.secret, t.other, t.checked, t.outcome

    # 輸入檢查：不呼叫 checker
    assert req("POST", "/api/auth/token", None, b"not json")[0] == 400
    status, body = post("/api/auth/token", {"nope": 1})
    assert status == 400 and "token" in body["error"]
    for bad in ("", "   ", "short", "x" * 600, "has space " + "y" * 30, "line\nbreak" + "y" * 30):
        status, body = post("/api/auth/token", {"token": bad})
        assert status == 400, bad
    assert req("POST", "/api/auth/token", {"Content-Type": "application/x-www-form-urlencoded"}, b"token=x")[0] == 415
    assert req("POST", "/auth/token", None, {"token": secret})[0] == 404
    assert not checked

    # 貼上有效的 token（前後有空白）：驗證 → 儲存，權限 600，回應不含完整 token
    status, body = post("/api/auth/token", {"token": f"  {secret}\n"})
    assert status == 200 and body["message"] == "驗證通過，已儲存", body
    assert checked == [secret] and body["status"]["state"] == "ok" and body["status"]["tail"] == secret[-4:]
    assert auth_store.load(site.auth.token_path)["token"] == secret
    assert os.name == "nt" or stat.S_IMODE(site.auth.token_path.stat().st_mode) == 0o600  # Windows 沒有 POSIX 權限位
    assert get("/api/auth")[1] == body["status"] and secret not in req("GET", "/api/auth")[1]
    status, health = get("/api/auth-health")
    assert status == 200 and health["state"] == "ok" and health["kind"] == "ok" and health["checked_at"], health

    # 驗證失敗：各種原因都不能洗掉原本的 token
    for v, want, text in (({"ok": False, "kind": "auth", "message": "401"}, 422, "授權失敗"),
                          ({"ok": False, "kind": "quota", "message": "429"}, 422, "額度用完"),
                          ({"ok": False, "kind": "other", "message": "boom"}, 422, "驗證失敗"),
                          ({"ok": False, "kind": "timeout", "message": "逾時"}, 504, "逾時"),
                          ({"ok": False, "kind": "env", "message": "沒有 SDK"}, 503, "SDK")):
        outcome["v"] = v
        status, body = post("/api/auth/token", {"token": other})
        assert status == want and text in body["error"] and other not in body["error"], (v, status, body)
        assert auth_store.load(site.auth.token_path)["token"] == secret, v
    assert checked[-1] == other
    assert get("/api/auth-health")[1] == health

    # 驗證正在進行時再送一個：409，不另開子程序
    outcome["v"] = {"ok": True, "kind": "ok", "message": ""}
    n = len(checked)
    assert site.auth._check_lock.acquire(blocking=False)
    try:
        status, body = post("/api/auth/token", {"token": other})
    finally:
        site.auth._check_lock.release()
    assert status == 409 and len(checked) == n and auth_store.load(site.auth.token_path)["token"] == secret

    # 測試連線：用已存的 token；失敗只回報，不改狀態
    status, body = post("/api/auth/test", {})
    assert status == 200 and body["message"] == "連線正常" and checked[-1] == secret
    outcome["v"] = {"ok": False, "kind": "auth", "message": "401"}
    status, body = post("/api/auth/test", {})
    assert status == 422 and auth_store.load(site.auth.token_path)["token"] == secret
    assert get("/api/auth-health")[1]["state"] == "invalid" and get("/api/auth-health")[1]["kind"] == "auth"
    outcome["v"] = {"ok": False, "kind": "quota", "message": "SECRET-DETAIL"}
    post("/api/auth/test", {})
    status, text = req("GET", "/api/auth-health")
    assert json.loads(text)["state"] == "unknown" and "SECRET-DETAIL" not in text
    outcome["v"] = {"ok": False, "kind": "api_key", "message": "x"}
    post("/api/auth/test", {})
    assert get("/api/auth-health")[1]["state"] == "invalid"
    outcome["v"] = {"ok": True, "kind": "ok", "message": ""}
    post("/api/auth/test", {})
    assert get("/api/auth-health")[1]["state"] == "ok"


def test_auth_lifecycle(t) -> None:
    from datetime import timedelta, timezone
    from unittest import mock

    site, get, post, secret, missing = t.site, t.get, t.post, t.secret, t.missing
    auth_store.save(secret, site.auth.token_path)
    assert post("/api/auth/test", {})[0] == 200  # 前提：驗證過一次，結果正常

    # 即將到期的提醒（以儲存時間推算）
    auth_store.save(secret, site.auth.token_path, datetime.now(timezone.utc) - timedelta(days=345))
    status, st = get("/api/auth")
    assert st["state"] == "expiring" and 0 <= st["days_left"] <= 30, st
    assert get("/api/auth-health")[1]["state"] == "expiring"
    auth_store.save(secret, site.auth.token_path, datetime.now(timezone.utc) - timedelta(days=400))
    assert get("/api/auth")[1]["state"] == "expired"
    assert get("/api/auth-health")[1]["state"] == "expiring"

    # 刪除：只移除本機檔案；沒有 token 時 test 是 404
    status, body = post("/api/auth/revoke", {})
    assert status == 200 and "已刪除" in body["message"] and body["status"]["state"] == "missing"
    assert not site.auth.token_path.exists()
    status, body = post("/api/auth/revoke", {})
    assert status == 200 and "本來就沒有" in body["message"]
    assert post("/api/auth/test", {})[0] == 404
    assert get("/api/auth-health")[1] == missing

    # 最近的驗證結果來自 state/auth_health.json（worker 定時寫）：web 重啟不會丟，太久沒更新就不能再說正常
    auth_store.save(secret, site.auth.token_path)
    assert get("/api/auth-health")[1] == {"state": "unknown", "kind": "unchecked", "checked_at": None}
    at = datetime.now(timezone.utc).replace(microsecond=0)
    auth_health.write(True, "ok", site.auth.health_path, at)
    assert get("/api/auth-health")[1] == {"state": "ok", "kind": "ok", "checked_at": at.isoformat()}
    assert Site(site.auth.health_path.parent.parent, checker=lambda _: {}).auth.health()["state"] == "ok"  # 新的 web 程序讀得到
    auth_health.write(False, "auth", site.auth.health_path, at)
    assert get("/api/auth-health")[1]["state"] == "invalid"
    late = at + STALE_AFTER + timedelta(seconds=1)
    assert site.auth.health(late) == {"state": "unknown", "kind": "stale", "checked_at": at.isoformat()}
    assert site.auth.health(at + STALE_AFTER)["state"] == "invalid"
    with mock.patch.dict(os.environ, {auth_store.TOKEN_ENV: "env-" + "e" * 20}):  # 環境變數裡的 token 不算來源
        auth_store.delete(site.auth.token_path)
        assert get("/api/auth-health")[1] == missing
        auth_store.save(secret, site.auth.token_path)
    assert post("/api/auth/revoke", {})[0] == 200 and not site.auth.health_path.exists()  # 刪 token 連結果一起清
    assert not hasattr(site.auth, "monitor") and not hasattr(site.auth, "check_now")  # 定時驗證在 worker
    auth_store.delete(site.auth.token_path)
