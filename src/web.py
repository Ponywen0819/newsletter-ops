#!/usr/bin/env python3
"""Web 層：瀏覽晨報，每則旁邊按 👍／👎，直接寫進 state/feedback.jsonl。

用法：python3 src/web.py [--host 127.0.0.1] [--port 8787] [--selftest]
port 也可用環境變數 NEWSLETTER_WEB_PORT 設定（--port 優先）。

頁面：/ 當日晨報、/reports 歷史列表、/reports/<date> 單日晨報、POST /feedback。
樣式與前端腳本在 src/static/（web.css、web.js），由 /static/<檔名> 提供。
版型沿用 render_email.render_body()；每則 `<!-- mark: uid=... -->` 的位置換成 👍／👎。

回饋規則：只有 `+`（👍）、`-`（👎）兩級；再按一次同一顆＝取消，按另一顆＝覆蓋。
一律 append 一行到 feedback.jsonl，以同一 uid 的最後一筆為準；取消寫成 mark ""。
欄位與 feedback.py 相同（共用 feedback.build_row）。
寫入與 feedback.py 的 collect() 共用 feedback.locked / append_rows：都只 append，可以同時跑。

不做登入：預設只 bind 127.0.0.1，對外交給 Cloudflare Tunnel + Access。
POST /feedback 只收 Content-Type: application/json（跨站表單送不出這種請求，順便擋 CSRF）。
只用 stdlib。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import feedback  # noqa: E402
import render_email  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARKS = {"+", "-", ""}  # "" ＝ 取消
MAX_BODY = 4096

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {  # 白名單：只供這兩個檔，網址不會直接對應檔案路徑
    "web.css": "text/css; charset=utf-8",
    "web.js": "text/javascript; charset=utf-8",
}


def level(mark: str) -> str:
    """feedback.jsonl 裡可能有 feedback.py 收進來的 ++ / --，網頁只分兩級。"""
    return mark[:1] if mark[:1] in ("+", "-") else ""


def buttons_html(uid: str, mark: str) -> str:
    def btn(value: str, emoji: str, label: str) -> str:
        pressed = "true" if mark == value else "false"
        return (f'<button type="button" class="fb-btn" data-mark="{value}" aria-pressed="{pressed}" '
                f'aria-label="{label}" title="{label}">{emoji}</button>')
    return (f'<div class="fb" data-uid="{uid}">{btn("+", "👍", "有用")}{btn("-", "👎", "沒用")}'
            f'<span class="fb-msg" role="status"></span></div>')


def page_html(title: str, inner: str, interactive: bool = False) -> str:
    nav = '<nav><a href="/">今日晨報</a><a href="/reports">歷史晨報</a></nav>'
    script = '<script src="/static/web.js" defer></script>' if interactive else ""
    return (f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title>'
            f'<link rel="stylesheet" href="/static/web.css">{script}</head>'
            f'<body style="{render_email.S["body"]}">{nav}'
            f'<div style="{render_email.S["card"]}">{inner}</div></body></html>')


def notice_html(title: str, message: str) -> str:
    return page_html(title, f'<h1 style="{render_email.S["h1"]}">{html.escape(title)}</h1>'
                            f'<p style="{render_email.S["p"]}">{message}</p>')


class NotFound(Exception):
    pass


class BadRequest(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Site:
    """所有讀寫都在這裡；Handler 只負責 HTTP。root 可換掉，selftest 用暫存目錄。"""

    def __init__(self, root: Path = ROOT, today: Callable[[], str] | None = None):
        self.root = root
        self.today = today or (lambda: f"{datetime.now(ZoneInfo('Asia/Taipei')):%Y-%m-%d}")
        self.feedback_path = root / "state" / "feedback.jsonl"

    def report_dates(self) -> list[str]:
        """reports/*.md 的日期，新到舊。"""
        stems = (p.stem for p in (self.root / "reports").glob("*.md"))
        return sorted((s for s in stems if DATE_RE.fullmatch(s)), reverse=True)

    def _report_path(self, date: str) -> Path:
        return self.root / "reports" / f"{date}.md"

    def marks(self) -> dict[str, str]:
        """uid → 目前的標記（'+' / '-'；沒標或已取消的不在裡面）。"""
        if not self.feedback_path.exists():
            return {}
        with feedback.locked(self.feedback_path):  # 避免讀到另一個程序寫到一半的列
            rows = feedback.read_feedback(self.feedback_path)
        return {uid: m for uid, row in rows.items() if (m := level(row.get("mark", "")))}

    def report_page(self, date: str) -> str:
        path = self._report_path(date)
        if not path.exists():
            raise NotFound(date)
        marks = self.marks()
        body, _, _ = render_email.render_body(
            path.read_text(encoding="utf-8"), lambda uid: buttons_html(uid, marks.get(uid, "")))
        return page_html(f"每日晨間簡報 {date}", body, interactive=True)

    def today_page(self) -> str:
        date = self.today()
        try:
            return self.report_page(date)
        except NotFound:
            latest = self.report_dates()
            hint = (f'最新一份是 <a href="/reports/{latest[0]}" style="{render_email.S["a"]}">{latest[0]}</a>。'
                    if latest else "目前還沒有任何晨報。")
            return notice_html(f"{date} 的晨報還沒產出", hint)

    def list_page(self) -> str:
        rows = []
        for date in self.report_dates():
            text = self._report_path(date).read_text(encoding="utf-8")
            line = next((ln for ln in text.splitlines() if "今日頭條" in ln), "")
            headline = render_email.plain(re.sub(r"^[>\s]*\**今日頭條[：:]\**\s*", "", line))
            rows.append(f'<li><a href="/reports/{date}">{date}</a><span>{html.escape(headline)}</span></li>')
        inner = (f'<h1 style="{render_email.S["h1"]}">歷史晨報</h1>'
                 f'<ul class="reports">{"".join(rows) or "<li>目前還沒有任何晨報。</li>"}</ul>')
        return page_html("歷史晨報", inner)

    def _find_report(self, uid: str) -> str | None:
        """含有這個 uid 的最新一份報告（日期）。"""
        needle = f"uid={uid}"
        for date in self.report_dates():
            if needle in self._report_path(date).read_text(encoding="utf-8"):
                return date
        return None

    def _curated_item(self, date: str, uid: str) -> dict:
        try:
            payload = json.loads((self.root / "data" / "curated" / f"{date}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return next((i for i in payload.get("items", []) if i.get("uid") == uid), {})

    def record(self, uid: str, mark: str) -> dict:
        """append 一列到 feedback.jsonl，回傳寫入的那一列。mark '' ＝ 取消。"""
        if mark not in MARKS:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "mark 只能是 +、- 或空字串（取消）")
        if not UID_RE.fullmatch(uid):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "uid 格式不符")
        date = self._find_report(uid)
        if date is None:
            raise BadRequest(HTTPStatus.NOT_FOUND, "任何一份報告裡都找不到這個 uid")
        row = feedback.build_row(uid, mark, f"{date}.md", self._curated_item(date, uid))
        with feedback.locked(self.feedback_path):
            feedback.append_rows(self.feedback_path, [row])
        return row


class Handler(BaseHTTPRequestHandler):
    server_version = "newsletter-web"

    @property
    def site(self) -> Site:
        return self.server.site  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)

    def _send(self, status: int, body: str, ctype: str = "text/html; charset=utf-8",
              cache: str = "no-store") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)  # 頁面的標記狀態會變，預設不快取
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, self.site.today_page())
            elif path == "/reports":
                self._send(200, self.site.list_page())
            elif m := re.fullmatch(r"/reports/(\d{4}-\d{2}-\d{2})", path):
                self._send(200, self.site.report_page(m[1]))
            elif path.startswith("/static/"):
                name = path[len("/static/"):]
                if name not in STATIC_TYPES:
                    raise NotFound(path)
                # 檔案很小；no-cache ＝ 每次向伺服器確認，改了 CSS／JS 重新整理就生效
                self._send(200, (STATIC_DIR / name).read_text(encoding="utf-8"), STATIC_TYPES[name], "no-cache")
            else:
                raise NotFound(path)
        except NotFound:
            self._send(404, notice_html("找不到頁面", '回 <a href="/reports">歷史晨報</a>。'))
        except ValueError as exc:  # render_body：報告 Markdown 格式不符
            self._send(500, notice_html("報告格式不符", html.escape(str(exc))))
        except Exception:
            traceback.print_exc()
            self._send(500, notice_html("伺服器錯誤", "詳見伺服器的 stderr。"))

    def do_POST(self) -> None:
        if urlsplit(self.path).path.rstrip("/") != "/feedback":
            self._send_json(404, {"error": "not found"})
            return
        try:
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type 必須是 application/json")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "缺 Content-Length") from None
            if not 0 < length <= MAX_BODY:
                raise BadRequest(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body 需在 1~{MAX_BODY} bytes")
            try:
                data = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise BadRequest(HTTPStatus.BAD_REQUEST, "body 不是合法的 JSON") from None
            if not (isinstance(data, dict) and isinstance(data.get("uid"), str) and isinstance(data.get("mark"), str)):
                raise BadRequest(HTTPStatus.BAD_REQUEST, "需要字串欄位 uid、mark")
            row = self.site.record(data["uid"], data["mark"])
            self._send_json(200, {"uid": row["uid"], "mark": row["mark"]})
        except BadRequest as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "server error"})


def make_server(site: Site, host: str, port: int, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.site = site  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def selftest() -> None:
    import tempfile
    import urllib.error
    import urllib.request

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
        site = Site(root, today=lambda: "2026-09-28")
        server = make_server(site, DEFAULT_HOST, 0, quiet=True)  # port 0：讓系統挑空的 port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://{DEFAULT_HOST}:{server.server_address[1]}"

        def get(path: str) -> tuple[int, str]:
            try:
                with urllib.request.urlopen(base + path) as resp:
                    return resp.status, resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8")

        def post(payload, ctype: str = "application/json") -> tuple[int, dict]:
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            req = urllib.request.Request(base + "/feedback", data=raw, method="POST",
                                         headers={"Content-Type": ctype})
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        def saved() -> dict[str, dict]:
            return feedback.read_feedback(root / "state" / "feedback.jsonl")

        def pressed(page: str, uid: str) -> str:
            """該則目前亮起的是哪一顆（'+' / '-' / ''）。"""
            box = re.search(rf'<div class="fb" data-uid="{uid}">(.*?)</div>', page, re.S)
            assert box, f"頁面裡找不到 {uid} 的按鈕"
            on = re.findall(r'data-mark="([+-])" aria-pressed="true"', box[1])
            assert len(on) <= 1, on
            return on[0] if on else ""

        try:
            # 頁面：按鈕插在每則 mark 註解的位置，email 版型不受影響
            for path in ("/", "/reports/2026-09-28"):
                status, page = get(path)
                assert status == 200 and page.count('class="fb"') == 3, (path, status)
                assert "&lt;標題&gt;" in page and "mark:" not in page
                assert [pressed(page, u) for u in (uid_a, uid_b, uid_c)] == ["", "", ""]
            # 巢狀子項目（"  - 細節"）之後的按鈕要回到最外層 <li>，不能掛在子項目裡
            assert re.search(r"細節</li></ul><div class=\"fb\" data-uid=\"%s\"" % uid_a, page), page
            assert "class=\"fb\"" not in render_email.render(md)[0]

            # CSS／JS 是獨立的靜態檔：頁面只引用、不內嵌；只供白名單裡的檔名
            assert '<link rel="stylesheet" href="/static/web.css">' in page
            assert '<script src="/static/web.js" defer></script>' in page
            assert "<style" not in page and "<script>" not in page
            for name, ctype, needle in (("web.css", "text/css", ".fb-btn"), ("web.js", "text/javascript", "/feedback")):
                with urllib.request.urlopen(f"{base}/static/{name}") as resp:
                    assert resp.status == 200 and resp.headers["Content-Type"].startswith(ctype), resp.headers
                    assert resp.headers["Cache-Control"] == "no-cache"
                    assert needle in resp.read().decode("utf-8")
            assert all(get(f"/static/{n}")[0] == 404 for n in ("nope.css", "web.py", "../web.py", ""))
            listing = get("/reports")[1]  # 沒有按鈕的頁面只載 CSS，不載 JS
            assert "/static/web.css" in listing and "web.js" not in listing and "<style" not in listing

            # /reports：新到舊、略過非日期檔名、顯示頭條
            status, listing = get("/reports")
            assert status == 200 and "notes" not in listing
            assert listing.index("2026-09-28") < listing.index("2026-09-27") and "某事發生，見 來源。" in listing
            assert get("/reports/2026-01-01")[0] == 404 and get("/reports/..%2Fconfig")[0] == 404
            assert get("/nope")[0] == 404

            # 寫入：欄位沿用 feedback.py，並帶出 curated 的中繼資料
            assert post({"uid": uid_a, "mark": "+"}) == (200, {"uid": uid_a, "mark": "+"})
            row = saved()[uid_a]
            assert row["mark"] == "+" and row["report"] == "2026-09-28.md" and row["title"] == "重點"
            assert row["topic"] == "ai-industry" and row["matched_keywords"] == ["llm"]
            assert set(row) == set(feedback.build_row(uid_a, "+", "x.md", {})), row
            assert pressed(get("/reports/2026-09-28")[1], uid_a) == "+"
            # curated 裡沒有的 uid 也能標，只是沒有中繼資料
            assert post({"uid": uid_b, "mark": "-"})[0] == 200
            assert saved()[uid_b]["mark"] == "-" and saved()[uid_b]["title"] == ""

            # 覆蓋：以最後一筆為準；檔案是 append，不是改寫
            assert post({"uid": uid_a, "mark": "-"})[0] == 200
            assert saved()[uid_a]["mark"] == "-"
            assert pressed(get("/")[1], uid_a) == "-" and pressed(get("/")[1], uid_b) == "-"
            lines = (root / "state" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
            assert [json.loads(ln)["mark"] for ln in lines if uid_a in ln] == ["+", "-"], lines

            # 取消：mark "" 讀回來是未標記
            assert post({"uid": uid_a, "mark": ""}) == (200, {"uid": uid_a, "mark": ""})
            assert saved()[uid_a]["mark"] == ""
            page = get("/reports/2026-09-28")[1]
            assert pressed(page, uid_a) == "" and pressed(page, uid_b) == "-"

            # feedback.py 收進來的 ++ / -- 在網頁上算同一級
            site.feedback_path.write_text(site.feedback_path.read_text(encoding="utf-8").rstrip("\n"),
                                          encoding="utf-8")  # 結尾沒換行也要能接著寫
            assert post({"uid": uid_c, "mark": "+"})[0] == 200
            assert all(ln.startswith("{") for ln in site.feedback_path.read_text(encoding="utf-8").splitlines())
            row = feedback.build_row(uid_c, "++", "2026-09-28.md", {})
            with site.feedback_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            assert pressed(get("/")[1], uid_c) == "+"

            # 擋掉不合法的請求，且不寫檔
            before = site.feedback_path.read_text(encoding="utf-8")
            assert post({"uid": uid_a, "mark": "++"})[0] == 400
            assert post({"uid": "xyz", "mark": "+"})[0] == 400
            assert post({"uid": "f" * 16, "mark": "+"})[0] == 404          # 報告裡沒有這個 uid
            assert post({"uid": uid_a})[0] == 400 and post([1, 2])[0] == 400
            assert post(b"not json")[0] == 400
            assert post({"uid": uid_a, "mark": "+"}, ctype="application/x-www-form-urlencoded")[0] == 415
            assert post(b"x" * (MAX_BODY + 1))[0] == 413
            assert site.feedback_path.read_text(encoding="utf-8") == before
        finally:
            server.shutdown()
            server.server_close()
    print("ok")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="晨報瀏覽與 👍／👎 回饋")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"預設 {DEFAULT_HOST}，不要綁到對外位址（沒有登入）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NEWSLETTER_WEB_PORT", DEFAULT_PORT)))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[web] 警告：綁在 {args.host}，這個服務沒有登入機制，任何連得到的人都能寫入回饋。", file=sys.stderr)
    server = make_server(Site(), args.host, args.port)
    print(f"[web] http://{args.host}:{server.server_address[1]}/  （Ctrl-C 結束）", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
