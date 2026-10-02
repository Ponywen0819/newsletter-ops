#!/usr/bin/env python3
"""Web 層：瀏覽晨報，每則旁邊按 👍／👎，直接寫進 state/feedback.jsonl。

用法：python3 src/web.py [--host 127.0.0.1] [--port 8787] [--selftest]
port 也可用環境變數 NEWSLETTER_WEB_PORT 設定（--port 優先）。

頁面：/ 當日晨報、/reports 歷史列表、/reports/<date> 單日晨報、POST /feedback。
/auth（只限本機）：貼上 `claude setup-token` 的 OAuth token，讓 agent_run.py 以訂閱額度執行；見下方。
樣式與前端腳本在 src/static/（web.css、web.js、auth.js），由 /static/<檔名> 提供。
版型沿用 render_email.render_body()；每則 `<!-- mark: uid=... -->` 的位置換成 👍／👎。

回饋規則：只有 `+`（👍）、`-`（👎）兩級；再按一次同一顆＝取消，按另一顆＝覆蓋。
一律 append 一行到 feedback.jsonl，以同一 uid 的最後一筆為準；取消寫成 mark ""。
欄位與 feedback.py 相同（共用 feedback.build_row）。
寫入與 feedback.py 的 collect() 共用 feedback.locked / append_rows：都只 append，可以同時跑。

不做登入：預設只 bind 127.0.0.1，對外交給 Cloudflare Tunnel + Access。
POST 只收 Content-Type: application/json（跨站表單送不出這種請求，順便擋 CSRF）。
只用 stdlib。

/auth 與 /auth/*（token、test、revoke）能寫入憑證，比 👍／👎 敏感得多，所以**只服務本機**（is_local_request），不符合一律回 404：
  1. 來源位址是 loopback
  2. Host 標頭是 127.0.0.1／localhost／[::1]
  3. 沒有 Cf-*、X-Forwarded-*、X-Real-IP、Forwarded、Via、Cdn-Loop 等代理標頭
  4. 有 Origin 的話，也必須是本機
cloudflared 跑在同一台機器、以 127.0.0.1 連進來，所以光看來源位址擋不住 Tunnel，要靠 2、3。
遠端 host 上要貼 token：`ssh -L 8787:127.0.0.1:8787 <host>` 後開 http://localhost:8787/auth，
或直接在 host 上設環境變數 CLAUDE_CODE_OAUTH_TOKEN。
貼上的 token 先交給 `agent_run.py --auth-check` 實際呼叫一次驗證，通過才儲存（舊 token 不會被無效的覆蓋）；
token 只經由環境變數傳給子程序（不上命令列）、不回傳給瀏覽器、不寫進 log。驗證需要 SDK，請用 `uv run src/web.py` 啟動。
"""
from __future__ import annotations

import argparse
import html
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
from typing import Callable, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import auth_store  # noqa: E402
import feedback  # noqa: E402
import render_email  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARKS = {"+", "-", ""}  # "" ＝ 取消
MAX_BODY = 4096
CHECK_TIMEOUT = 90  # 秒；驗證 token 的子程序最多等這麼久（無效 token 約 2 秒，有效的幾秒；卡住要放棄）

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {  # 白名單：只供這幾個檔，網址不會直接對應檔案路徑
    "web.css": "text/css; charset=utf-8",
    "web.js": "text/javascript; charset=utf-8",
    "auth.js": "text/javascript; charset=utf-8",
}

# POST 路由 → 是否只限本機。/auth/* 能寫入憑證，只給本機。
POST_ROUTES = {"/feedback": False, "/auth/token": True, "/auth/test": True, "/auth/revoke": True}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
PROXY_HEADERS = {"x-real-ip", "forwarded", "via", "cdn-loop"}  # 另外 cf-*、x-forwarded-* 開頭的也算
AUTH_NAV_SLOT = "<!--auth-nav-->"  # 導覽列的 /auth 連結只給本機請求，送出前才換掉


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
    """用 `agent_run.py --auth-check` 實際呼叫一次來驗證 token。回傳 {ok, kind, message}。
    token 只放在子程序的環境變數（不上命令列，ps 看不到）；子程序自己會移除環境裡的 API key 等。"""
    cmd = [sys.executable, str(ROOT / "src" / "agent_run.py"), "--auth-check", "--token-from-env"]
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
    if p.returncode == 2:  # agent_run.py 在沒有 SDK 時、還沒走到驗證就以 2 結束
        return {"ok": False, "kind": "env", "message": "驗證需要 claude-agent-sdk，請改用 `uv run src/web.py` 啟動"}
    return {"ok": False, "kind": "other", "message": f"驗證程序異常結束（exit {p.returncode}）"}


def page_html(title: str, inner: str, interactive: bool = False, script_src: str = "web.js") -> str:
    nav = f'<nav><a href="/">今日晨報</a><a href="/reports">歷史晨報</a>{AUTH_NAV_SLOT}</nav>'
    script = f'<script src="/static/{script_src}" defer></script>' if interactive else ""
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

    def __init__(self, root: Path = ROOT, today: Callable[[], str] | None = None,
                 checker: Callable[[str], dict] | None = None):
        self.root = root
        self.today = today or (lambda: f"{datetime.now(ZoneInfo('Asia/Taipei')):%Y-%m-%d}")
        self.feedback_path = root / "state" / "feedback.jsonl"
        self.token_path = auth_store.token_path(root)
        self.checker = checker or run_auth_check  # selftest 換成假的，不真的呼叫 Claude
        self._check_lock = threading.Lock()       # 同時只跑一個驗證，避免被連按開出一堆子程序

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

    # ---- /auth：OAuth token（只限本機，由 Handler 擋）----

    def auth_status_html(self) -> str:
        st = auth_store.status(self.token_path)
        rows = []
        if st["state"] == "missing":
            rows.append('<dt>狀態</dt><dd class="auth-state auth-missing">尚未授權</dd>')
        else:
            label = {"ok": "已授權",
                     "expiring": "即將到期（剩 %d 天），請重新貼上新的 token" % st.get("days_left", 0),
                     "expired": "預計已過期，請重新貼上新的 token"}[st["state"]]
            rows.append(f'<dt>狀態</dt><dd class="auth-state auth-{st["state"]}">{html.escape(label)}</dd>')
            rows.append(f'<dt>Token</dt><dd><code>…{html.escape(st["tail"])}</code>（只顯示尾 4 碼）</dd>')
            rows.append(f'<dt>儲存日期</dt><dd>{st["saved_at"]}</dd>')
            rows.append(f'<dt>預計到期</dt><dd>{st["expires_at"]}'
                        f'<span class="auth-hint">（以一年效期推算，實際以伺服器為準）</span></dd>')
        note = ""
        if st["state"] == "missing" and st["env_token"]:
            note = (f'<p class="auth-note">這個程序的環境有 <code>{auth_store.TOKEN_ENV}</code>；'
                    f'排程若也設了同一個環境變數，agent_run.py 會用它（優先序在這裡儲存的 token 之後）。</p>')
        return f'<dl class="auth-status">{"".join(rows)}</dl>{note}'

    def auth_page(self) -> str:
        S = render_email.S
        inner = f"""<h1 style="{S['h1']}">Claude 授權</h1>
<p style="{S['p']}">無人值守執行（<code style="{S['code']}">agent_run.py</code>）只用 OAuth 的訂閱額度，<strong>不使用 API key</strong>，
也不會因為額度用完而自動改用別的認證方式。這個頁面只能從本機開啟。</p>
<div id="auth-status" aria-live="polite">{self.auth_status_html()}</div>
<h2 style="{S['h2']}">貼上 token</h2>
<ol class="auth-steps">
<li>在<strong>自己的電腦</strong>開終端機，執行 <code style="{S['code']}">claude setup-token</code></li>
<li>在瀏覽器完成授權</li>
<li>複製終端機印出的 token（只會印一次，CLI 不會幫你存）</li>
<li>貼到下面送出。伺服器會先實際呼叫一次 Claude 驗證（用掉極少的訂閱額度），通過才儲存</li>
</ol>
<form id="auth-form" autocomplete="off">
<input id="auth-token" type="password" name="token" autocomplete="off" spellcheck="false"
       placeholder="貼上 token" aria-label="OAuth token" required>
<button type="submit" class="auth-btn auth-primary">驗證並儲存</button>
</form>
<p id="auth-msg" class="auth-msg" role="status"></p>
<div class="auth-actions">
<button type="button" class="auth-btn" data-action="test">測試連線</button>
<button type="button" class="auth-btn auth-danger" data-action="revoke">刪除已存的 token</button>
</div>
<p class="auth-note">token 需要 Pro／Max／Team／Enterprise 方案，效期一年；到期前這裡會提醒。
訂閱有使用額度，額度用完時晨報會失敗（exit 6），不會自動改用 API key。
刪除只會移除這裡儲存的檔案，token 在 Anthropic 端仍然有效。</p>"""
        return page_html("Claude 授權", inner, interactive=True, script_src="auth.js")

    def _failure(self, result: dict) -> BadRequest:
        kind, msg = result.get("kind"), result.get("message", "")
        if kind == "timeout":
            return BadRequest(HTTPStatus.GATEWAY_TIMEOUT, msg)
        if kind == "env":
            return BadRequest(HTTPStatus.SERVICE_UNAVAILABLE, msg)
        if kind == "auth":
            return BadRequest(HTTPStatus.UNPROCESSABLE_ENTITY, f"授權失敗：{msg}（token 可能貼錯、已撤銷或已過期）")
        if kind == "quota":
            return BadRequest(HTTPStatus.UNPROCESSABLE_ENTITY, f"額度用完，現在無法驗證：{msg}（稍後再試）")
        return BadRequest(HTTPStatus.UNPROCESSABLE_ENTITY, f"驗證失敗：{msg}")

    def _verify(self, token: str) -> None:
        """跑一次驗證；沒通過就丟 BadRequest。"""
        if not self._check_lock.acquire(blocking=False):
            raise BadRequest(HTTPStatus.CONFLICT, "另一個驗證正在進行，請稍候再試")
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
            raise BadRequest(HTTPStatus.BAD_REQUEST, problem)
        self._verify(token)
        auth_store.save(token, self.token_path)
        return {"message": "驗證通過，已儲存", "html": self.auth_status_html()}

    def test_token(self) -> dict:
        rec = auth_store.load(self.token_path)
        if rec is None:
            raise BadRequest(HTTPStatus.NOT_FOUND, "還沒有儲存的 token")
        self._verify(rec["token"])
        return {"message": "連線正常", "html": self.auth_status_html()}

    def revoke_token(self) -> dict:
        """只刪掉這裡儲存的檔；token 在 Anthropic 端仍然有效。"""
        removed = auth_store.delete(self.token_path)
        return {"message": "已刪除儲存的 token" if removed else "本來就沒有儲存的 token", "html": self.auth_status_html()}


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

    def _send(self, status: int, body: str, ctype: str = "text/html; charset=utf-8",
              cache: str = "no-store") -> None:
        if AUTH_NAV_SLOT in body:
            body = body.replace(AUTH_NAV_SLOT, '<a href="/auth">Claude 授權</a>' if self._local() else "")
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
            elif path == "/auth":
                if not self._local():
                    raise NotFound(path)
                self._send(200, self.site.auth_page())
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

    def _read_json(self):
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type 必須是 application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "缺 Content-Length") from None
        if not 0 < length <= MAX_BODY:
            raise BadRequest(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body 需在 1~{MAX_BODY} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "body 不是合法的 JSON") from None

    def do_POST(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        local_only = POST_ROUTES.get(path)
        if local_only is None or (local_only and not self._local()):
            self._send_json(404, {"error": "not found"})  # /auth/* 對非本機請求＝不存在
            return
        try:
            data = self._read_json()
            if path == "/feedback":
                if not (isinstance(data, dict) and isinstance(data.get("uid"), str) and isinstance(data.get("mark"), str)):
                    raise BadRequest(HTTPStatus.BAD_REQUEST, "需要字串欄位 uid、mark")
                row = self.site.record(data["uid"], data["mark"])
                self._send_json(200, {"uid": row["uid"], "mark": row["mark"]})
            elif path == "/auth/token":
                if not (isinstance(data, dict) and isinstance(data.get("token"), str)):
                    raise BadRequest(HTTPStatus.BAD_REQUEST, "需要字串欄位 token")
                self._send_json(200, self.site.submit_token(data["token"]))
            elif path == "/auth/test":
                self._send_json(200, self.site.test_token())
            else:  # /auth/revoke
                self._send_json(200, self.site.revoke_token())
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
    selftest_auth()
    print("ok")


def selftest_auth() -> None:
    import http.client
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
        site = Site(root, today=lambda: "2026-09-28", checker=checker)
        server = make_server(site, DEFAULT_HOST, 0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        def req(method, path, headers=None, body=None):
            conn = http.client.HTTPConnection(DEFAULT_HOST, port, timeout=10)
            h, data = dict(headers or {}), None
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                h.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=h)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8")
            conn.close()
            seen.append(text)
            return resp.status, text

        def post(path, body, headers=None):
            status, text = req("POST", path, headers, body)
            return status, json.loads(text)

        try:
            # 本機：頁面、導覽列連結、靜態檔
            status, page = req("GET", "/auth")
            assert status == 200 and "Claude 授權" in page and "尚未授權" in page and "/static/auth.js" in page
            assert "<style" not in page and "<script>" not in page and AUTH_NAV_SLOT not in page
            assert 'type="password"' in page and 'autocomplete="off"' in page and "不使用 API key" in page
            assert 'href="/auth"' in req("GET", "/")[1] and 'href="/auth"' in req("GET", "/reports")[1]
            status, js = req("GET", "/static/auth.js")
            assert status == 200 and "/auth/token" in js and "Storage" not in js

            # 非本機（公開網域的 Host、代理標頭、跨站 Origin）：/auth 全部是 404，導覽列也沒有連結
            for hdr in ({"Host": "news.example.com"},
                        {"Cf-Ray": "abc", "Cf-Connecting-Ip": "1.2.3.4"},
                        {"X-Forwarded-For": "1.2.3.4"},
                        {"Cdn-Loop": "cloudflare"},
                        {"Origin": "https://evil.example"}):
                assert req("GET", "/auth", hdr)[0] == 404, hdr
                assert req("GET", "/auth/", hdr)[0] == 404, hdr
                for path in ("/auth/token", "/auth/test", "/auth/revoke"):
                    status, body = post(path, {"token": secret}, hdr)
                    assert status == 404 and body == {"error": "not found"}, (hdr, path)
                nav_page = req("GET", "/", hdr)[1]
                assert "/auth" not in nav_page and AUTH_NAV_SLOT not in nav_page, hdr
            assert not checked and not site.token_path.exists()  # 被擋的請求沒有觸發驗證、沒有寫檔
            # 一般功能不受影響：經 Tunnel（公開網域）一樣能按 👍／👎
            assert post("/feedback", {"uid": uid, "mark": "+"}, {"Host": "news.example.com", "Cf-Ray": "abc"})[0] == 200

            # 輸入檢查：不呼叫 checker
            assert req("POST", "/auth/token", None, b"not json")[0] == 400  # Content-Type 是 JSON、內容不是
            status, body = post("/auth/token", {"nope": 1})
            assert status == 400 and "token" in body["error"]
            for bad in ("", "   ", "short", "x" * 600, "has space " + "y" * 30, "line\nbreak" + "y" * 30):
                status, body = post("/auth/token", {"token": bad})
                assert status == 400, bad
            assert req("POST", "/auth/token", {"Content-Type": "application/x-www-form-urlencoded"}, b"token=x")[0] == 415
            assert not checked

            # 貼上有效的 token（前後有空白）：驗證 → 儲存，權限 600，回應不含完整 token
            status, body = post("/auth/token", {"token": f"  {secret}\n"})
            assert status == 200 and body["message"] == "驗證通過，已儲存", body
            assert checked == [secret] and "已授權" in body["html"] and secret[-4:] in body["html"]
            assert auth_store.load(site.token_path)["token"] == secret
            assert stat.S_IMODE(site.token_path.stat().st_mode) == 0o600
            assert "已授權" in req("GET", "/auth")[1] and secret not in req("GET", "/auth")[1]

            # 驗證失敗：各種原因都不能洗掉原本的 token
            other = "sk-ant-oat01-" + "Zz1" * 14
            for v, want, text in (({"ok": False, "kind": "auth", "message": "401"}, 422, "授權失敗"),
                                  ({"ok": False, "kind": "quota", "message": "429"}, 422, "額度用完"),
                                  ({"ok": False, "kind": "other", "message": "boom"}, 422, "驗證失敗"),
                                  ({"ok": False, "kind": "timeout", "message": "逾時"}, 504, "逾時"),
                                  ({"ok": False, "kind": "env", "message": "沒有 SDK"}, 503, "SDK")):
                outcome["v"] = v
                status, body = post("/auth/token", {"token": other})
                assert status == want and text in body["error"] and other not in body["error"], (v, status, body)
                assert auth_store.load(site.token_path)["token"] == secret, v
            assert checked[-1] == other

            # 驗證正在進行時再送一個：409，不另開子程序
            outcome["v"] = {"ok": True, "kind": "ok", "message": ""}
            n = len(checked)
            assert site._check_lock.acquire(blocking=False)
            try:
                status, body = post("/auth/token", {"token": other})
            finally:
                site._check_lock.release()
            assert status == 409 and len(checked) == n and auth_store.load(site.token_path)["token"] == secret

            # 測試連線：用已存的 token；失敗只回報，不改狀態
            status, body = post("/auth/test", {})
            assert status == 200 and body["message"] == "連線正常" and checked[-1] == secret
            outcome["v"] = {"ok": False, "kind": "auth", "message": "401"}
            status, body = post("/auth/test", {})
            assert status == 422 and auth_store.load(site.token_path)["token"] == secret

            # 即將到期的提醒（以儲存時間推算）
            auth_store.save(secret, site.token_path, datetime.now(timezone.utc) - timedelta(days=345))
            page = req("GET", "/auth")[1]
            assert "即將到期" in page and "auth-expiring" in page
            auth_store.save(secret, site.token_path, datetime.now(timezone.utc) - timedelta(days=400))
            assert "預計已過期" in req("GET", "/auth")[1]

            # 刪除：只移除本機檔案；沒有 token 時 test 是 404
            status, body = post("/auth/revoke", {})
            assert status == 200 and "已刪除" in body["message"] and "尚未授權" in body["html"] and not site.token_path.exists()
            status, body = post("/auth/revoke", {})
            assert status == 200 and "本來就沒有" in body["message"]
            assert post("/auth/test", {})[0] == 404

            # 完整 token 不能出現在任何回應裡
            assert not any(secret in text or other in text for text in seen)
            # 👍／👎 仍照常寫入，與 /auth 互不干擾
            assert uid in (root / "state" / "feedback.jsonl").read_text(encoding="utf-8")
        finally:
            server.shutdown()
            server.server_close()


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
