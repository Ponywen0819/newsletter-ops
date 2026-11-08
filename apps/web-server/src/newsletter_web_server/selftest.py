"""newsletter-web --selftest：真的 HTTP 伺服器＋暫存目錄，不呼叫 Claude。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from newsletter_shared import auth_health, auth_store, feedback

from . import server
from .auth import STALE_AFTER
from .server import IMMUTABLE, MAX_BODY, Site, is_local_request, served


def selftest() -> None:
    import tempfile

    uid_a, uid_b, uid_c = "0123456789abcdef", "1111111111111111", "2222222222222222"
    md = f"""# 每日晨間簡報 2026-09-28

<!-- subject: 測試頭條 -->

> **今日頭條：** 某事發生，見 [來源](https://a.example/x)。

## 科技與 AI

**[重點 <標題>](https://a.example/1)**

- 發生什麼：A
  - 細節
<!-- mark:    uid={uid_a} -->

## 其餘收錄

- **[其他](https://a.example/2)** — 半句
<!-- mark:    uid={uid_b} -->
- **[其他二](https://a.example/3)** — 第二則
<!-- mark:    uid={uid_c} -->
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reports").mkdir()
        (root / "data" / "curated").mkdir(parents=True)
        (root / "reports" / "2026-09-28.md").write_text(md, encoding="utf-8")
        (root / "reports" / "2026-09-27.md").write_text(md.replace("2026-09-28", "2026-09-27"), encoding="utf-8")
        (root / "reports" / "notes.md").write_text("不是日期檔名，要被略過", encoding="utf-8")
        (root / "data" / "curated" / "2026-09-28.json").write_text(json.dumps(
            {"items": [{"uid": uid_a, "title": "重點", "source": "S1", "topic": "ai-industry",
                        "matched_keywords": ["llm"]}]}, ensure_ascii=False), encoding="utf-8")
        # 前端的建置結果（假的）；dist 之外放一個檔案，確認拿不到
        (root / "apps" / "web-ui" / "dist" / "assets").mkdir(parents=True)
        shell = '<!doctype html><div id="root"></div><script type="module" src="/assets/app-abc123.js"></script>'
        (root / "apps" / "web-ui" / "dist" / "index.html").write_text(shell, encoding="utf-8")
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
        site = Site(root, today=lambda: "2026-09-28")

        with served(site) as req:
            _selftest_reports(req, site, root, uid_a)
            _selftest_static(req, shell)
            _selftest_feedback(req, site, root, shell, (uid_a, uid_b, uid_c))
            _selftest_etag(req, uid_a)

        # 還沒建置前端：頁面回 503 並說明怎麼建置，API 照常
        bare = root / "bare"
        (bare / "reports").mkdir(parents=True)
        with served(Site(bare, today=lambda: "2026-09-28")) as req:
            status, _, body = req("GET", "/")
            assert status == 503 and "npm run build" in body
            status, _, body = req("GET", "/api/today")
            assert status == 200 and json.loads(body) == {"date": "2026-09-28", "report": None, "marks": {}, "latest": None,
                                                     "run": None}
            assert json.loads(req("GET", "/api/reports")[2]) == {"reports": []}
    selftest_auth()
    print("ok")


def _selftest_etag(req, uid_a: str) -> None:
    """離線副本的條件請求：沒變回 304（沒有 body），標記或晨報變了 ETag 跟著變。"""
    path = "/api/reports/2026-09-28"
    status, headers, _ = req("GET", path)
    etag = headers["etag"]
    assert status == 200 and etag.startswith('"') and headers["cache-control"] == "no-store", headers
    for sent in (etag, f"W/{etag}", f'"x", {etag}', "*"):  # Cloudflare 可能改成弱 ETag
        status, headers, again = req("GET", path, {"If-None-Match": sent})
        assert status == 304 and again == "" and headers["etag"] == etag, (sent, status)
    assert req("GET", path, {"If-None-Match": '"nope"'})[0] == 200
    assert req("GET", "/api/reports")[1].get("etag") and req("GET", "/api/today")[1].get("etag")
    assert "etag" not in req("GET", "/api/session")[1]  # 只有同步的對象有
    req("POST", "/api/feedback", {"Content-Type": "application/json"}, {"uid": uid_a, "mark": "+"})
    status, headers, _ = req("GET", path, {"If-None-Match": etag})
    assert status == 200 and headers["etag"] != etag  # 標記變了
    req("POST", "/api/feedback", {"Content-Type": "application/json"}, {"uid": uid_a, "mark": ""})
    assert req("GET", path, {"If-None-Match": etag})[0] == 304  # 改回去就和原本一樣


def _selftest_reports(req, site: Site, root: Path, uid_a: str) -> None:
    def get(path: str, headers=None) -> tuple[int, dict]:
        status, _, text = req("GET", path, headers)
        return status, json.loads(text)

    # 晨報：回結構，不是 HTML；巢狀子項目不帶 mark，mark 掛在最外層的項目上（與 email 版型一致）
    status, data = get("/api/reports/2026-09-28")
    assert status == 200 and data["date"] == "2026-09-28" and data["marks"] == {}, data
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

    # 當日：有就給整份，沒有就給最新一份的日期
    assert get("/api/today") == (200, {**data, "latest": "2026-09-28", "run": None})
    site.reports.today = lambda: "2026-10-05"
    assert get("/api/today") == (200, {"date": "2026-10-05", "report": None, "marks": {}, "latest": "2026-09-28",
                                       "run": None})

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


def _selftest_static(req, shell: str) -> None:
    # 前端：路由一律回 index.html（含 CSP）；沒有行內腳本與樣式
    for path in ("/", "/reports", "/reports/", "/reports/2026-09-28", "/reports/2026-01-01", "/auth"):
        status, headers, body = req("GET", path)
        assert status == 200 and body == shell, (path, status)
        assert headers["content-type"] == "text/html; charset=utf-8" and headers["cache-control"] == "no-cache"
        assert "default-src 'self'" in headers["content-security-policy"] and "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "<style" not in shell and "<script>" not in shell
    # 不認得的路徑：同一份 index.html，但狀態碼是 404（前端畫「找不到頁面」）
    for path in ("/nope", "/reports/abc", "/api", "/reports/2026-09-28/x"):
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


def _selftest_feedback(req, site: Site, root: Path, shell: str, uids: tuple[str, str, str]) -> None:
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


def selftest_auth() -> None:
    secret = "sk-ant-oat01-" + "Kp4" * 14
    _selftest_is_local()
    _selftest_auth_http(secret)


def _selftest_is_local() -> None:
    # --- is_local_request：來源位址、Host、代理標頭、Origin ---
    ok_host = {"Host": "127.0.0.1:8787"}
    for ip, h in (("127.0.0.1", ok_host), ("::1", {"Host": "[::1]:8787"}), ("::ffff:127.0.0.1", {"Host": "localhost"}),
                  ("127.0.0.1", {"host": "LOCALHOST:1"}),
                  ("127.0.0.1", {"Host": "localhost:1", "Origin": "http://localhost:1"})):
        assert is_local_request(ip, h), (ip, h)
    for ip, h in (("192.168.1.5", ok_host), ("10.0.0.2", ok_host), ("not-an-ip", ok_host), ("", ok_host),
                  ("127.0.0.1", {}), ("127.0.0.1", {"Host": ""}),
                  ("127.0.0.1", {"Host": "news.example.com"}), ("127.0.0.1", {"Host": "news.example.com:443"}),
                  ("127.0.0.1", {"Host": "127.0.0.1.evil.com"}), ("127.0.0.1", {"Host": "localhost.evil.com"}),
                  ("127.0.0.1", {"Host": "evil.com@127.0.0.1"}), ("127.0.0.1", {"Host": "localhost:abc"}),
                  ("127.0.0.1", {**ok_host, "Origin": "https://evil.example"}), ("127.0.0.1", {**ok_host, "Origin": "null"}),
                  ("127.0.0.1", {**ok_host, "Origin": "http://localhost.evil.com"})):
        assert not is_local_request(ip, h), (ip, h)
    for name in ("Cf-Connecting-Ip", "Cf-Ray", "Cf-Access-Authenticated-User-Email", "CF-IPCountry", "X-Forwarded-For",
                 "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-Ip", "Forwarded", "Via", "Cdn-Loop"):
        assert not is_local_request("127.0.0.1", {**ok_host, name: "x"}), name
        assert not is_local_request("172.18.0.1", {**ok_host, name: "x"}, admin=True), name
    # 管理 port：來源放寬到私有位址（Docker bridge 閘道），公網位址、Host／Origin／代理標頭的檢查不變
    for ip in ("172.18.0.1", "192.168.1.5", "10.0.0.2", "127.0.0.1", "::1"):
        assert is_local_request(ip, ok_host, admin=True), ip
        assert is_local_request(ip, {**ok_host, "Origin": "http://127.0.0.1:8788"}, admin=True), ip
    for ip, h in (("8.8.8.8", ok_host), ("2001:4860:4860::8888", ok_host), ("not-an-ip", ok_host),
                  ("172.18.0.1", {"Host": "news.example.com"}), ("172.18.0.1", {}),
                  ("172.18.0.1", {**ok_host, "Origin": "https://evil.example"})):
        assert not is_local_request(ip, h, admin=True), (ip, h)
    assert not is_local_request("172.18.0.1", ok_host)


def _selftest_auth_http(secret: str) -> None:
    import tempfile
    from types import SimpleNamespace

    # --- 整個 HTTP 流程（假的 checker，不呼叫 Claude）---
    uid = "0123456789abcdef"
    checked: list[str] = []
    outcome = {"v": {"ok": True, "kind": "ok", "message": "驗證通過"}}

    def checker(token: str) -> dict:
        checked.append(token)
        return outcome["v"]

    seen: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
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
                                checked=checked, outcome=outcome, uid=uid, missing=missing)
            _selftest_auth_access(t)
            _selftest_auth_token(t)
            _selftest_auth_lifecycle(t)

            # 完整 token 不能出現在任何回應裡
            assert not any(secret in text or other in text for text in seen)
            # 👍／👎 仍照常寫入，與 /auth 互不干擾
            assert uid in (root / "state" / "feedback.jsonl").read_text(encoding="utf-8")


def _selftest_auth_access(t) -> None:
    from unittest import mock

    site, req, get, post, secret, checked, uid, missing = t.site, t.req, t.get, t.post, t.secret, t.checked, t.uid, t.missing

    # 本機：session、狀態、頁面
    assert get("/api/session") == (200, {"local": True})
    assert get("/api/auth") == (200, {"state": "missing"})
    assert req("GET", "/auth")[0] == 200

    # 非本機（公開網域的 Host、代理標頭、跨站 Origin）：/auth 與 /api/auth* 全部是 404，session 說不是本機
    for hdr in ({"Host": "news.example.com"},
                {"Cf-Ray": "abc", "Cf-Connecting-Ip": "1.2.3.4"},
                {"X-Forwarded-For": "1.2.3.4"},
                {"Cdn-Loop": "cloudflare"},
                {"Origin": "https://evil.example"}):
        assert req("GET", "/auth", hdr)[0] == 404 and req("GET", "/auth/", hdr)[0] == 404, hdr
        assert get("/api/auth", hdr) == (404, {"error": "not found"}), hdr
        assert get("/api/session", hdr) == (200, {"local": False}), hdr
        for path in ("/api/auth/token", "/api/auth/test", "/api/auth/revoke"):
            status, body = post(path, {"token": secret}, hdr)
            assert status == 404 and body == {"error": "not found"}, (hdr, path)
    assert not checked and not site.auth.token_path.exists()

    # 管理 port（--admin-port）：同一個 Site，/auth 與 /api/auth* 在這裡才算本機；Host／代理標頭／Origin 照擋
    with served(site, admin=True) as admin_req:
        def admin_get(path, headers=None):
            status, _, text = admin_req("GET", path, headers)
            return status, json.loads(text) if text.startswith("{") else text

        assert admin_get("/api/session") == (200, {"local": True})
        assert admin_get("/api/auth") == (200, {"state": "missing"})
        assert admin_req("GET", "/auth")[0] == 200
        status, _, text = admin_req("POST", "/api/auth/revoke", None, {})
        assert status == 200 and "本來就沒有" in text
        for hdr in ({"Host": "news.example.com"}, {"Cf-Ray": "abc"}, {"X-Forwarded-For": "1.2.3.4"},
                    {"Origin": "https://evil.example"}):
            assert admin_req("GET", "/auth", hdr)[0] == 404, hdr
            assert admin_get("/api/auth", hdr) == (404, {"error": "not found"}), hdr
            assert admin_req("POST", "/api/auth/revoke", hdr, {})[0] == 404, hdr
        # 實際的來源位址進不來（測試只能從 127.0.0.1 連），改驗 Handler 有把「這是管理 port」交給判斷
        real, calls = server.is_local_request, []
        with mock.patch.object(server, "is_local_request", lambda *a: calls.append(a[2]) or real(*a)):
            admin_get("/api/session")
            get("/api/session")
        assert calls == [True, False], calls

    # 認證健康狀態是給所有人看的（橫幅用）：非本機也讀得到；沒有任何認證來源＝missing
    assert get("/api/auth-health") == (200, missing)
    assert get("/api/auth-health", {"Host": "news.example.com", "Cf-Ray": "abc"}) == (200, missing)
    # 一般功能不受影響：經 Tunnel（公開網域）一樣能讀報告、按 👍／👎
    tunnel = {"Host": "news.example.com", "Cf-Ray": "abc"}
    assert get("/api/today", tunnel)[0] == 200 and req("GET", "/", tunnel)[0] == 200
    assert get("/api/heartbeat", tunnel) == (200, {"ok": True})
    assert post("/api/feedback", {"uid": uid, "mark": "+"}, tunnel)[0] == 200


def _selftest_auth_token(t) -> None:
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
    assert stat.S_IMODE(site.auth.token_path.stat().st_mode) == 0o600
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


def _selftest_auth_lifecycle(t) -> None:
    from datetime import timedelta, timezone
    from unittest import mock

    site, get, post, secret, missing = t.site, t.get, t.post, t.secret, t.missing

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
