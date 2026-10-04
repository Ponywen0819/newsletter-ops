#!/usr/bin/env python3
"""Web 層：JSON API ＋ 提供前端（web/，Vite + React）的建置結果。晨報每則旁邊按 👍／👎，直接寫進 state/feedback.jsonl。

用法：python3 src/web.py [--host 127.0.0.1] [--port 8787] [--selftest]
port 也可用環境變數 NEWSLETTER_WEB_PORT 設定（--port 優先）。

前端是 web/ 底下的 Vite + React 專案，要先建置：`cd web && npm install && npm run build`，
這裡把 web/dist 當靜態檔提供（/assets/* 帶 hash，長期快取；index.html 每次確認）。
前端的路由（/、/reports、/reports/<date>、/feedback/<uid>、/auth）一律回 index.html，由前端自己畫；沒建置過時回 503 並說明怎麼建置。
開發前端用 `cd web && npm run dev`（Vite dev server，把 /api 代理到這裡），不必每次重新建置。

API（都是 JSON；前端的型別在 web/src/types.ts）：
  GET  /api/session           {local}                 這個請求是不是從本機來（前端據此決定要不要顯示「Claude 授權」）
  GET  /api/today             {date, latest, report, marks}   當日晨報；還沒產出時 report 是 null、latest 是最新一份的日期
  GET  /api/reports           {reports: [{date, headline}]}   歷史列表，新到舊
  GET  /api/reports/<date>    {date, report, marks}   report 是 report_data.parse_report() 的結構（不是 HTML）
  GET  /api/feedback/<uid>    {uid, date, title, mark}  email 連結的確認頁用：這則的標題與目前標記（只讀，不寫入）
  POST /api/feedback          {uid, mark}             👍／👎
  GET  /api/auth              {state, ...}            OAuth token 的狀態（只限本機）
  POST /api/auth/token|test|revoke                    貼上、測試、刪除 OAuth token（只限本機）
晨報的 Markdown 由 report_data.py 解析成結構（網頁與 email 共用同一份），版型由前端負責；email 版型由 render_email.py 排版，兩者互不影響。

email 裡的 👍／👎 連結（render_email.py，需設 NEWSLETTER_BASE_URL）指向 /feedback/<uid>?v=…：
GET 只畫確認頁（信箱的安全掃描會自動開連結，所以 GET 絕不寫入），頁面上再按一次才 POST /api/feedback。

回饋規則：只有 `+`（👍）、`-`（👎）兩級；再按一次同一顆＝取消，按另一顆＝覆蓋。
一律 append 一行到 feedback.jsonl，以同一 uid 的最後一筆為準；取消寫成 mark ""。
欄位與 feedback.py 相同（共用 feedback.build_row）。
寫入與 feedback.py 的 collect() 共用 feedback.locked / append_rows：都只 append，可以同時跑。

不做登入：預設只 bind 127.0.0.1，對外交給 Cloudflare Tunnel + Access。
POST 只收 Content-Type: application/json（跨站表單送不出這種請求，順便擋 CSRF）。
HTML 回應帶 Content-Security-Policy（只許同源的腳本與樣式），前端因此不能有行內 <script>／style。
只用 stdlib。

/auth 與 /api/auth*（token、test、revoke）能寫入憑證，比 👍／👎 敏感得多，所以**只服務本機**（is_local_request），不符合一律 404：
  1. 來源位址是 loopback
  2. Host 標頭是 127.0.0.1／localhost／[::1]
  3. 沒有 Cf-*、X-Forwarded-*、X-Real-IP、Forwarded、Via、Cdn-Loop 等代理標頭
  4. 有 Origin 的話，也必須是本機
cloudflared 跑在同一台機器、以 127.0.0.1 連進來，所以光看來源位址擋不住 Tunnel，要靠 2、3。
遠端 host 上要貼 token：`ssh -L 8787:127.0.0.1:8787 <host>` 後開 http://localhost:8787/auth，
或直接在 host 上設環境變數 CLAUDE_CODE_OAUTH_TOKEN。
貼上的 token 先交給 `newsletter-agent --auth-check` 實際呼叫一次驗證，通過才儲存（舊 token 不會被無效的覆蓋）；
token 只經由環境變數傳給子程序（不上命令列）、不回傳給瀏覽器、不寫進 log。驗證需要 SDK，請用 `uv run src/web.py` 啟動。
"""
from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from newsletter_shared import auth_store, feedback, report_data

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARKS = {"+", "-", ""}  # "" ＝ 取消
MAX_BODY = 4096
CHECK_TIMEOUT = 90  # 秒；驗證 token 的子程序最多等這麼久（無效 token 約 2 秒，有效的幾秒；卡住要放棄）

# 前端的路由（web/src/App.tsx）；這幾條都回 index.html。其他不認得的路徑也回 index.html，但狀態碼是 404，由前端畫「找不到頁面」。
SPA_ROUTES = re.compile(r"/|/reports|/reports/\d{4}-\d{2}-\d{2}|/feedback/[0-9a-f]{16}|/auth")
BUILD_COMMAND = "cd web && npm install && npm run build"
STATIC_TYPES = {  # 副檔名白名單：dist 裡只有這些會被提供
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
IMMUTABLE = "public, max-age=31536000, immutable"  # /assets/* 的檔名帶內容 hash，內容變了檔名就變
CSP = "default-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"

# POST 路由 → 是否只限本機。/api/auth/* 能寫入憑證，只給本機。
POST_ROUTES = {"/api/feedback": False, "/api/auth/token": True, "/api/auth/test": True, "/api/auth/revoke": True}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
PROXY_HEADERS = {"x-real-ip", "forwarded", "via", "cdn-loop"}  # 另外 cf-*、x-forwarded-* 開頭的也算


def level(mark: str) -> str:
    """feedback.jsonl 裡可能有 feedback.py 收進來的 ++ / --，網頁只分兩級。"""
    return mark[:1] if mark[:1] in ("+", "-") else ""


def local_host(value: str) -> bool:
    """Host／Origin 的主機部分是不是本機（可帶 port）。"""
    if not value or "@" in value or "/" in value:
        return False
    try:
        parts = urlsplit("//" + value)
        parts.port  # noqa: B018  port 不合法會丟 ValueError
    except ValueError:
        return False
    return parts.hostname in LOCAL_HOSTNAMES


def is_local_request(client_ip: str, headers: Mapping[str, str]) -> bool:
    """/auth 的存取條件：真的從本機來，而且不是經 Cloudflare Tunnel（或任何代理）轉進來的。
    cloudflared 跑在同一台機器、以 127.0.0.1 連進來，所以來源位址不夠，要再看 Host 與代理標頭。"""
    try:
        addr = ipaddress.ip_address(client_ip.split("%")[0])
    except ValueError:
        return False
    addr = getattr(addr, "ipv4_mapped", None) or addr  # ::ffff:127.0.0.1
    if not addr.is_loopback:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    if not local_host(h.get("host", "")):
        return False
    if "origin" in h:
        origin = urlsplit(h["origin"])
        if origin.scheme not in ("http", "https") or not local_host(origin.netloc):
            return False
    return not any(k.startswith(("cf-", "x-forwarded-")) or k in PROXY_HEADERS for k in h)


def run_auth_check(token: str) -> dict:
    """用 `newsletter-agent --auth-check` 實際呼叫一次來驗證 token。回傳 {ok, kind, message}。
    token 只放在子程序的環境變數（不上命令列，ps 看不到）；子程序自己會移除環境裡的 API key 等。"""
    cmd = [sys.executable, "-m", "newsletter_agent.agent_run", "--auth-check", "--token-from-env"]
    env = {**os.environ, auth_store.TOKEN_ENV: token}
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "kind": "timeout", "message": f"驗證逾時（超過 {CHECK_TIMEOUT} 秒）"}
    except OSError as exc:
        return {"ok": False, "kind": "env", "message": f"無法啟動驗證程序：{exc.strerror or type(exc).__name__}"}
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and isinstance(result.get("ok"), bool):
            return {"ok": result["ok"], "kind": str(result.get("kind", "other")),
                    "message": str(result.get("message", "")).replace(token, "***")}
    if p.returncode == 2:  # agent_run 在沒有 SDK 時、還沒走到驗證就以 2 結束
        return {"ok": False, "kind": "env", "message": "驗證需要 claude-agent-sdk，請改用 `uv run src/web.py` 啟動"}
    return {"ok": False, "kind": "other", "message": f"驗證程序異常結束（exit {p.returncode}）"}


class NotFound(Exception):
    pass


class ApiError(Exception):
    """帶狀態碼的錯誤，Handler 轉成 {"error": message}。"""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Site:
    """所有讀寫都在這裡；Handler 只負責 HTTP。root 可換掉，selftest 用暫存目錄。"""

    def __init__(self, root: Path = ROOT, today: Callable[[], str] | None = None,
                 checker: Callable[[str], dict] | None = None):
        self.root = root
        self.dist = root / "web" / "dist"
        self.today = today or (lambda: f"{datetime.now(ZoneInfo('Asia/Taipei')):%Y-%m-%d}")
        self.feedback_path = root / "state" / "feedback.jsonl"
        self.token_path = auth_store.token_path(root)
        self.checker = checker or run_auth_check  # selftest 換成假的，不真的呼叫 Claude
        self._check_lock = threading.Lock()       # 同時只跑一個驗證，避免被連按開出一堆子程序

    # ---- 前端的建置結果 ----

    def index_html(self) -> bytes | None:
        try:
            return (self.dist / "index.html").read_bytes()
        except OSError:
            return None

    def static_file(self, url_path: str) -> tuple[bytes, str, str] | None:
        """dist 裡的檔案 → (內容, Content-Type, Cache-Control)；不在 dist 裡、不是檔案、副檔名不在白名單都是 None。
        路徑不做 URL 解碼，所以 `..%2F` 只是一個不存在的檔名；resolve 後再確認仍在 dist 底下，連 symlink 都擋。"""
        dist = self.dist.resolve()
        try:
            target = (dist / url_path.lstrip("/")).resolve()
            ctype = STATIC_TYPES.get(target.suffix.lower())
            if ctype is None or not target.is_relative_to(dist) or not target.is_file():
                return None
            cache = IMMUTABLE if target.relative_to(dist).parts[0] == "assets" else "no-cache"
            return target.read_bytes(), ctype, cache
        except (OSError, ValueError):
            return None

    # ---- 晨報 ----

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

    def report_payload(self, date: str) -> dict:
        try:
            text = self._report_path(date).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NotFound(date) from None
        try:
            report = report_data.parse_report(text)
        except ValueError as exc:  # Markdown 格式不符 SKILL.md 規定的子集
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"報告格式不符：{exc}") from None
        shown = set(report_data.uids_of(report))
        return {"date": date, "report": report, "marks": {u: m for u, m in self.marks().items() if u in shown}}

    def today_payload(self) -> dict:
        date = self.today()
        latest = self.report_dates()
        try:
            payload = self.report_payload(date)
        except NotFound:
            payload = {"date": date, "report": None, "marks": {}}
        return {**payload, "latest": latest[0] if latest else None}

    def list_payload(self) -> dict:
        rows = []
        for date in self.report_dates():
            text = self._report_path(date).read_text(encoding="utf-8")
            line = next((ln for ln in text.splitlines() if "今日頭條" in ln), "")
            headline = report_data.plain(re.sub(r"^[>\s]*\**今日頭條[：:]\**\s*", "", line))
            rows.append({"date": date, "headline": headline})
        return {"reports": rows}

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

    def feedback_target(self, uid: str) -> dict:
        """email 連結的確認頁要顯示的資料。任何一份報告裡都沒有這個 uid ＝ NotFound。"""
        date = self._find_report(uid) if UID_RE.fullmatch(uid) else None
        if date is None:
            raise NotFound(uid)
        return {"uid": uid, "date": date, "title": self._curated_item(date, uid).get("title", ""),
                "mark": self.marks().get(uid, "")}

    def record(self, uid: str, mark: str) -> dict:
        """append 一列到 feedback.jsonl，回傳寫入的那一列。mark '' ＝ 取消。"""
        if mark not in MARKS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "mark 只能是 +、- 或空字串（取消）")
        if not UID_RE.fullmatch(uid):
            raise ApiError(HTTPStatus.BAD_REQUEST, "uid 格式不符")
        date = self._find_report(uid)
        if date is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "任何一份報告裡都找不到這個 uid")
        row = feedback.build_row(uid, mark, f"{date}.md", self._curated_item(date, uid))
        with feedback.locked(self.feedback_path):
            feedback.append_rows(self.feedback_path, [row])
        return row

    # ---- /auth：OAuth token（只限本機，由 Handler 擋）----

    def auth_status(self) -> dict:
        """給前端畫的狀態，不含 token（最多尾 4 碼）。state：missing / ok / expiring / expired。"""
        return auth_store.status(self.token_path)

    def _failure(self, result: dict) -> ApiError:
        kind, msg = result.get("kind"), result.get("message", "")
        if kind == "timeout":
            return ApiError(HTTPStatus.GATEWAY_TIMEOUT, msg)
        if kind == "env":
            return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, msg)
        if kind == "auth":
            return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"授權失敗：{msg}（token 可能貼錯、已撤銷或已過期）")
        if kind == "quota":
            return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"額度用完，現在無法驗證：{msg}（稍後再試）")
        return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"驗證失敗：{msg}")

    def _verify(self, token: str) -> None:
        """跑一次驗證；沒通過就丟 ApiError。"""
        if not self._check_lock.acquire(blocking=False):
            raise ApiError(HTTPStatus.CONFLICT, "另一個驗證正在進行，請稍候再試")
        try:
            result = self.checker(token)
        finally:
            self._check_lock.release()
        if not result.get("ok"):
            raise self._failure(result)

    def submit_token(self, raw: str) -> dict:
        """貼上新的 token：先驗證，通過才覆寫。無效的 token 不會洗掉原本可用的。"""
        token = auth_store.clean(raw)
        if (problem := auth_store.check_format(token)) is not None:
            raise ApiError(HTTPStatus.BAD_REQUEST, problem)
        self._verify(token)
        auth_store.save(token, self.token_path)
        return {"message": "驗證通過，已儲存", "status": self.auth_status()}

    def test_token(self) -> dict:
        rec = auth_store.load(self.token_path)
        if rec is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "還沒有儲存的 token")
        self._verify(rec["token"])
        return {"message": "連線正常", "status": self.auth_status()}

    def revoke_token(self) -> dict:
        """只刪掉這裡儲存的檔；token 在 Anthropic 端仍然有效。"""
        removed = auth_store.delete(self.token_path)
        return {"message": "已刪除儲存的 token" if removed else "本來就沒有儲存的 token", "status": self.auth_status()}


class Handler(BaseHTTPRequestHandler):
    server_version = "newsletter-web"

    @property
    def site(self) -> Site:
        return self.server.site  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)

    def _local(self) -> bool:
        return is_local_request(self.client_address[0], self.headers)

    def _send(self, status: int, body: str | bytes, ctype: str = "text/plain; charset=utf-8",
              cache: str = "no-store", headers: Mapping[str, str] | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)  # API 的內容（標記狀態）會變，預設不快取
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _send_shell(self, status: int = 200) -> None:
        """前端的 index.html。沒建置過就說明怎麼建置（API 不受影響）。"""
        index = self.site.index_html()
        if index is None:
            hint = (f"<!DOCTYPE html><meta charset=\"utf-8\"><title>前端尚未建置</title>"
                    f"<p>找不到 <code>web/dist</code>。先建置前端：<code>{BUILD_COMMAND}</code></p>")
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, hint, "text/html; charset=utf-8")
            return
        self._send(status, index, "text/html; charset=utf-8", "no-cache", {"Content-Security-Policy": CSP})

    def do_GET(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/"):
            self._get_api(path)
        elif SPA_ROUTES.fullmatch(path):
            # /auth 對非本機＝不存在，連頁面都不給（前端會畫「找不到頁面」）
            self._send_shell(404 if path == "/auth" and not self._local() else 200)
        elif (found := self.site.static_file(path)) is not None:
            body, ctype, cache = found
            self._send(200, body, ctype, cache)
        elif "." in path.rsplit("/", 1)[-1]:  # 像是檔案的路徑（/assets/舊版.js）：給 HTML 只會讓瀏覽器更困惑
            self._send(404, "not found")
        else:
            self._send_shell(404)

    def _get_api(self, path: str) -> None:
        try:
            if path == "/api/session":
                payload = {"local": self._local()}
            elif path == "/api/today":
                payload = self.site.today_payload()
            elif path == "/api/reports":
                payload = self.site.list_payload()
            elif m := re.fullmatch(r"/api/reports/(\d{4}-\d{2}-\d{2})", path):
                payload = self.site.report_payload(m[1])
            elif m := re.fullmatch(r"/api/feedback/([0-9a-f]{16})", path):
                payload = self.site.feedback_target(m[1])
            elif path == "/api/auth" and self._local():
                payload = self.site.auth_status()
            else:
                raise NotFound(path)
            self._send_json(200, payload)
        except NotFound:
            self._send_json(404, {"error": "not found"})
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "server error"})

    def _read_json(self):
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type 必須是 application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "缺 Content-Length") from None
        if not 0 < length <= MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body 需在 1~{MAX_BODY} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "body 不是合法的 JSON") from None

    def do_POST(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        local_only = POST_ROUTES.get(path)
        if local_only is None or (local_only and not self._local()):
            self._send_json(404, {"error": "not found"})  # /api/auth/* 對非本機請求＝不存在
            return
        try:
            data = self._read_json()
            if path == "/api/feedback":
                if not (isinstance(data, dict) and isinstance(data.get("uid"), str) and isinstance(data.get("mark"), str)):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "需要字串欄位 uid、mark")
                row = self.site.record(data["uid"], data["mark"])
                self._send_json(200, {"uid": row["uid"], "mark": row["mark"]})
            elif path == "/api/auth/token":
                if not (isinstance(data, dict) and isinstance(data.get("token"), str)):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "需要字串欄位 token")
                self._send_json(200, self.site.submit_token(data["token"]))
            elif path == "/api/auth/test":
                self._send_json(200, self.site.test_token())
            else:  # /api/auth/revoke
                self._send_json(200, self.site.revoke_token())
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "server error"})


def make_server(site: Site, host: str, port: int, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.site = site  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


@contextlib.contextmanager
def served(site: Site) -> Iterator[Callable[..., tuple[int, dict[str, str], str]]]:
    """selftest 用：起一個真的 HTTP 伺服器，給一個 req(method, path, headers, body) → (狀態碼, 標頭, 內文)。
    用 http.client 而不是 urllib：路徑原樣送出（不正規化 `..`），標頭也能自己指定（Host、Cf-* 等）。"""
    import http.client

    server = make_server(site, DEFAULT_HOST, 0, quiet=True)  # port 0：讓系統挑空的 port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def req(method: str, path: str, headers: Mapping[str, str] | None = None, body=None) -> tuple[int, dict[str, str], str]:
        h, data = dict(headers or {}), None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection(DEFAULT_HOST, port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode("utf-8")
        finally:
            conn.close()

    try:
        yield req
    finally:
        server.shutdown()
        server.server_close()


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
        (root / "web" / "dist" / "assets").mkdir(parents=True)
        shell = '<!doctype html><div id="root"></div><script type="module" src="/assets/app-abc123.js"></script>'
        (root / "web" / "dist" / "index.html").write_text(shell, encoding="utf-8")
        (root / "web" / "dist" / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
        (root / "web" / "dist" / "assets" / "app-abc123.css").write_text("body{}", encoding="utf-8")
        (root / "web" / "dist" / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        (root / "web" / "dist" / "build.py").write_text("print('不該被提供')", encoding="utf-8")
        (root / "web" / "secret.txt").write_text("dist 之外", encoding="utf-8")  # 一層 .. 就到
        (root / "secret.txt").write_text("dist 之外", encoding="utf-8")          # 三層 .. 才到
        site = Site(root, today=lambda: "2026-09-28")

        with served(site) as req:
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
            assert "重點 <標題>" in raw and "mark:" not in raw and "<div" not in raw  # 原文照給，跳脫是前端的事

            # 當日：有就給整份，沒有就給最新一份的日期
            assert get("/api/today") == (200, {**data, "latest": "2026-09-28"})
            site.today = lambda: "2026-10-05"
            assert get("/api/today") == (200, {"date": "2026-10-05", "report": None, "marks": {}, "latest": "2026-09-28"})
            site.today = lambda: "2026-09-28"

            # 歷史列表：新到舊、略過非日期檔名、顯示頭條
            status, data = get("/api/reports")
            assert status == 200 and data == {"reports": [{"date": "2026-09-28", "headline": "某事發生，見 來源。"},
                                                          {"date": "2026-09-27", "headline": "某事發生，見 來源。"}]}, data
            assert get("/api/reports/2026-01-01")[0] == 404 and get("/api/reports/..%2Fconfig")[0] == 404
            assert get("/api/nope") == (404, {"error": "not found"}) and get("/api/reports/")[0] == 200

            # 格式不符的報告：單日回 500 與原因，列表照樣能列出（頭條抓不到就留空）
            (root / "reports" / "2026-09-26.md").write_text("# 壞掉的報告\n", encoding="utf-8")
            status, data = get("/api/reports/2026-09-26")
            assert status == 500 and "報告格式不符" in data["error"] and "頭條" in data["error"], data
            assert get("/api/reports")[1]["reports"][-1] == {"date": "2026-09-26", "headline": ""}
            (root / "reports" / "2026-09-26.md").unlink()

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
            # 擋掉：不存在的檔案給純文字 404（不是 HTML）、dist 之外、副檔名不在白名單、目錄
            for path in ("/assets/missing.js", "/../secret.txt", "/assets/../../secret.txt", "/assets/../../../secret.txt",
                         "/assets/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt", "/secret.txt", "/build.py", "/assets", "/assets/"):
                status, headers, body = req("GET", path)
                assert status == 404 and "dist 之外" not in body and "不該被提供" not in body, (path, status)
                if "." in path.rsplit("/", 1)[-1]:
                    assert headers["content-type"].startswith("text/plain") and body == "not found", path

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
            site.feedback_path.write_text(site.feedback_path.read_text(encoding="utf-8").rstrip("\n"),
                                          encoding="utf-8")  # 結尾沒換行也要能接著寫
            assert post({"uid": uid_c, "mark": "+"})[0] == 200
            assert all(ln.startswith("{") for ln in site.feedback_path.read_text(encoding="utf-8").splitlines())
            with site.feedback_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(feedback.build_row(uid_c, "++", "2026-09-28.md", {})) + "\n")
                fh.write(json.dumps(feedback.build_row("f" * 16, "+", "2026-08-01.md", {})) + "\n")
            assert marks() == {uid_b: "-", uid_c: "+"}

            # email 連結的確認頁：GET 只讀，絕不寫入；標題來自 curated，沒有就空字串；mark 是目前的標記
            before = site.feedback_path.read_text(encoding="utf-8")
            assert get(f"/api/feedback/{uid_a}") == (200, {"uid": uid_a, "date": "2026-09-28", "title": "重點", "mark": ""})
            assert get(f"/api/feedback/{uid_b}")[1] == {"uid": uid_b, "date": "2026-09-28", "title": "", "mark": "-"}
            assert get(f"/api/feedback/{uid_c}")[1]["mark"] == "+"  # ++ 在網頁上算同一級
            for path in (f"/api/feedback/{'e' * 16}", "/api/feedback/xyz", "/api/feedback/", "/api/feedback"):
                assert get(path)[0] == 404, path  # 報告裡沒有的 uid、格式不符
            for v in ("%2B", "-", "+"):  # 連結本身（含 ?v=）回前端的 index.html，同樣不寫入
                status, _, body = req("GET", f"/feedback/{uid_a}?v={v}")
                assert status == 200 and body == shell, (v, status)
            assert req("GET", f"/feedback/{'e' * 16}")[0] == 200  # 頁面照給，找不到由前端畫
            assert req("GET", "/feedback/xyz")[0] == 404 and req("GET", "/feedback")[0] == 404
            assert site.feedback_path.read_text(encoding="utf-8") == before

            # 擋掉不合法的請求，且不寫檔
            assert post({"uid": uid_a, "mark": "++"})[0] == 400
            assert post({"uid": "xyz", "mark": "+"})[0] == 400
            assert post({"uid": "e" * 16, "mark": "+"})[0] == 404          # 報告裡沒有這個 uid
            assert post({"uid": uid_a})[0] == 400 and post([1, 2])[0] == 400
            assert post(b"not json")[0] == 400
            assert post({"uid": uid_a, "mark": "+"}, ctype="application/x-www-form-urlencoded")[0] == 415
            assert post(b"x" * (MAX_BODY + 1))[0] == 413
            assert req("POST", "/feedback", None, {"uid": uid_a, "mark": "+"})[0] == 404  # 舊路徑不存在
            assert site.feedback_path.read_text(encoding="utf-8") == before

        # 還沒建置前端：頁面回 503 並說明怎麼建置，API 照常
        bare = root / "bare"
        (bare / "reports").mkdir(parents=True)
        with served(Site(bare, today=lambda: "2026-09-28")) as req:
            status, _, body = req("GET", "/")
            assert status == 503 and "npm run build" in body
            status, _, body = req("GET", "/api/today")
            assert status == 200 and json.loads(body) == {"date": "2026-09-28", "report": None, "marks": {}, "latest": None}
            assert json.loads(req("GET", "/api/reports")[2]) == {"reports": []}
    selftest_auth()
    print("ok")


def selftest_auth() -> None:
    import stat
    import subprocess as sp
    import tempfile
    from datetime import timedelta, timezone
    from unittest import mock

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

    # --- run_auth_check：子程序的呼叫方式與輸出解析（假的 subprocess.run）---
    secret = "sk-ant-oat01-" + "Kp4" * 14

    def fake_run(stdout="", code=0, raises=None):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            if raises:
                raise raises
            return sp.CompletedProcess(cmd, code, stdout, "")
        return run, calls

    run, calls = fake_run('[agent] 雜訊\n{"ok": false, "kind": "auth", "message": "bad ' + secret + '", "source": "env-token"}\n', 5)
    with mock.patch.object(sp, "run", run):
        res = run_auth_check(secret)
    assert res == {"ok": False, "kind": "auth", "message": "bad ***"}, res
    cmd, kw = calls[0]
    assert secret not in " ".join(cmd) and "--auth-check" in cmd and "--token-from-env" in cmd
    assert cmd[1:3] == ["-m", "newsletter_agent.agent_run"] and kw["cwd"] == ROOT, cmd  # 以模組呼叫、cwd 是 repo 根
    assert kw["env"][auth_store.TOKEN_ENV] == secret and kw["timeout"] == CHECK_TIMEOUT and kw["stdin"] == sp.DEVNULL
    run, _ = fake_run('{"ok": true, "kind": "ok", "message": "驗證通過", "source": "env-token"}\n')
    with mock.patch.object(sp, "run", run):
        assert run_auth_check(secret)["ok"] is True
    run, _ = fake_run("", 2)
    with mock.patch.object(sp, "run", run):
        assert run_auth_check(secret)["kind"] == "env"
    run, _ = fake_run("garbage", 1)
    with mock.patch.object(sp, "run", run):
        assert run_auth_check(secret) == {"ok": False, "kind": "other", "message": "驗證程序異常結束（exit 1）"}
    run, _ = fake_run(raises=sp.TimeoutExpired("x", CHECK_TIMEOUT))
    with mock.patch.object(sp, "run", run):
        assert run_auth_check(secret)["kind"] == "timeout"
    run, _ = fake_run(raises=FileNotFoundError(2, "No such file"))
    with mock.patch.object(sp, "run", run):
        assert run_auth_check(secret)["kind"] == "env"

    # --- 整個 HTTP 流程（假的 checker，不呼叫 Claude）---
    uid = "0123456789abcdef"
    checked: list[str] = []
    outcome = {"v": {"ok": True, "kind": "ok", "message": "驗證通過"}}

    def checker(token: str) -> dict:
        checked.append(token)
        return outcome["v"]

    seen: list[str] = []  # 所有回應內容，最後確認 token 沒有漏出去

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reports").mkdir()
        (root / "reports" / "2026-09-28.md").write_text(
            f"# 每日晨間簡報 2026-09-28\n\n<!-- subject: x -->\n\n> **今日頭條：** y\n\n## 科技\n\n"
            f"**[t](https://a.example/1)**\n\n- a\n<!-- mark:    uid={uid} -->\n", encoding="utf-8")
        (root / "web" / "dist").mkdir(parents=True)
        (root / "web" / "dist" / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
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

            # 本機：session、狀態、頁面
            assert get("/api/session") == (200, {"local": True})
            assert get("/api/auth") == (200, {"state": "missing", "env_token": False})
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
            assert not checked and not site.token_path.exists()  # 被擋的請求沒有觸發驗證、沒有寫檔
            # 一般功能不受影響：經 Tunnel（公開網域）一樣能讀報告、按 👍／👎
            tunnel = {"Host": "news.example.com", "Cf-Ray": "abc"}
            assert get("/api/today", tunnel)[0] == 200 and req("GET", "/", tunnel)[0] == 200
            assert post("/api/feedback", {"uid": uid, "mark": "+"}, tunnel)[0] == 200

            # 輸入檢查：不呼叫 checker
            assert req("POST", "/api/auth/token", None, b"not json")[0] == 400  # Content-Type 是 JSON、內容不是
            status, body = post("/api/auth/token", {"nope": 1})
            assert status == 400 and "token" in body["error"]
            for bad in ("", "   ", "short", "x" * 600, "has space " + "y" * 30, "line\nbreak" + "y" * 30):
                status, body = post("/api/auth/token", {"token": bad})
                assert status == 400, bad
            assert req("POST", "/api/auth/token", {"Content-Type": "application/x-www-form-urlencoded"}, b"token=x")[0] == 415
            assert req("POST", "/auth/token", None, {"token": secret})[0] == 404  # 舊路徑不存在
            assert not checked

            # 貼上有效的 token（前後有空白）：驗證 → 儲存，權限 600，回應不含完整 token
            status, body = post("/api/auth/token", {"token": f"  {secret}\n"})
            assert status == 200 and body["message"] == "驗證通過，已儲存", body
            assert checked == [secret] and body["status"]["state"] == "ok" and body["status"]["tail"] == secret[-4:]
            assert auth_store.load(site.token_path)["token"] == secret
            assert stat.S_IMODE(site.token_path.stat().st_mode) == 0o600
            assert get("/api/auth")[1] == body["status"] and secret not in req("GET", "/api/auth")[1]

            # 驗證失敗：各種原因都不能洗掉原本的 token
            other = "sk-ant-oat01-" + "Zz1" * 14
            for v, want, text in (({"ok": False, "kind": "auth", "message": "401"}, 422, "授權失敗"),
                                  ({"ok": False, "kind": "quota", "message": "429"}, 422, "額度用完"),
                                  ({"ok": False, "kind": "other", "message": "boom"}, 422, "驗證失敗"),
                                  ({"ok": False, "kind": "timeout", "message": "逾時"}, 504, "逾時"),
                                  ({"ok": False, "kind": "env", "message": "沒有 SDK"}, 503, "SDK")):
                outcome["v"] = v
                status, body = post("/api/auth/token", {"token": other})
                assert status == want and text in body["error"] and other not in body["error"], (v, status, body)
                assert auth_store.load(site.token_path)["token"] == secret, v
            assert checked[-1] == other

            # 驗證正在進行時再送一個：409，不另開子程序
            outcome["v"] = {"ok": True, "kind": "ok", "message": ""}
            n = len(checked)
            assert site._check_lock.acquire(blocking=False)
            try:
                status, body = post("/api/auth/token", {"token": other})
            finally:
                site._check_lock.release()
            assert status == 409 and len(checked) == n and auth_store.load(site.token_path)["token"] == secret

            # 測試連線：用已存的 token；失敗只回報，不改狀態
            status, body = post("/api/auth/test", {})
            assert status == 200 and body["message"] == "連線正常" and checked[-1] == secret
            outcome["v"] = {"ok": False, "kind": "auth", "message": "401"}
            status, body = post("/api/auth/test", {})
            assert status == 422 and auth_store.load(site.token_path)["token"] == secret

            # 即將到期的提醒（以儲存時間推算）
            auth_store.save(secret, site.token_path, datetime.now(timezone.utc) - timedelta(days=345))
            status, st = get("/api/auth")
            assert st["state"] == "expiring" and 0 <= st["days_left"] <= 30, st
            auth_store.save(secret, site.token_path, datetime.now(timezone.utc) - timedelta(days=400))
            assert get("/api/auth")[1]["state"] == "expired"

            # 刪除：只移除本機檔案；沒有 token 時 test 是 404
            status, body = post("/api/auth/revoke", {})
            assert status == 200 and "已刪除" in body["message"] and body["status"]["state"] == "missing"
            assert not site.token_path.exists()
            status, body = post("/api/auth/revoke", {})
            assert status == 200 and "本來就沒有" in body["message"]
            assert post("/api/auth/test", {})[0] == 404

            # 完整 token 不能出現在任何回應裡
            assert not any(secret in text or other in text for text in seen)
            # 👍／👎 仍照常寫入，與 /auth 互不干擾
            assert uid in (root / "state" / "feedback.jsonl").read_text(encoding="utf-8")


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
    site = Site()
    if site.index_html() is None:
        print(f"[web] 找不到 web/dist，頁面會回 503。先建置前端：{BUILD_COMMAND}", file=sys.stderr)
    server = make_server(site, args.host, args.port)
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
