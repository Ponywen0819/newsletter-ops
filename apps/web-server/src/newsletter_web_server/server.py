"""HTTP 層：路由、本機判斷、提供 apps/web-ui/dist。讀寫都交給 Site 底下的 reports 與 auth。"""
from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import re
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator, Mapping
from urllib.parse import parse_qs, urlsplit

from newsletter_shared import ROOT

from .access import HEADER as ACCESS_HEADER
from .access import Access
from .auth import Auth
from .errors import ApiError, NotFound
from .reports import Reports

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY = 4096
# 與 apps/web-ui/src/App.tsx 的路由同步
SPA_ROUTES = re.compile(r"/|/reports|/reports/\d{4}-\d{2}-\d{2}|/feedback/[0-9a-f]{16}(?:,[0-9a-f]{16})*|/stats|/auth")
# 離線副本同步的對象：回 ETag，If-None-Match 相符回 304（前端自己帶，所以照舊 no-store）
ETAG_PATHS = re.compile(r"/api/today|/api/reports|/api/reports/\d{4}-\d{2}-\d{2}")


def etag_matches(header: str, etag: str) -> bool:
    """If-None-Match 可能是逗號分隔的清單、*、或被代理（Cloudflare）改成弱 ETag 的 W/"..."。"""
    tags = {t.strip().removeprefix("W/") for t in header.split(",")}
    return "*" in tags or etag in tags
BUILD_COMMAND = "cd apps/web-ui && npm install && npm run build"
STATIC_TYPES = {  # 副檔名白名單
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
IMMUTABLE = "public, max-age=31536000, immutable"
CSP = "default-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"

# POST 路由 → 是否只限本機
POST_ROUTES = {"/api/feedback": False, "/api/kg-flag": False, "/api/auth/token": True, "/api/auth/test": True, "/api/auth/revoke": True}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
PROXY_HEADERS = {"x-real-ip", "forwarded", "via", "cdn-loop"}  # 另外 cf-*、x-forwarded-* 開頭的也算


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


def is_local_request(client_ip: str, headers: Mapping[str, str], admin: bool = False) -> bool:
    """/auth 的存取條件。cloudflared 也是從 127.0.0.1 連進來，所以還要看 Host 與代理標頭。
    admin：Docker 轉進來的來源是 bridge 閘道，只要求私有位址。"""
    try:
        addr = ipaddress.ip_address(client_ip.split("%", maxsplit=1)[0])
    except ValueError:
        return False
    addr = getattr(addr, "ipv4_mapped", None) or addr  # ::ffff:127.0.0.1
    if not (addr.is_private if admin else addr.is_loopback):
        return False
    h = {k.lower(): v for k, v in headers.items()}
    if not local_host(h.get("host", "")):
        return False
    if "origin" in h:
        origin = urlsplit(h["origin"])
        if origin.scheme not in ("http", "https") or not local_host(origin.netloc):
            return False
    return not any(k.startswith(("cf-", "x-forwarded-")) or k in PROXY_HEADERS for k in h)


class Site:
    """Handler 用到的全部狀態。root 可換掉，selftest 用暫存目錄。"""

    def __init__(self, root: Path = ROOT, today: Callable[[], str] | None = None,
                 checker: Callable[[str], dict] | None = None, access: Access | None = None):
        self.access = access  # 有設就驗 Cloudflare Access 的 JWT（本機請求不驗）
        self.reports = Reports(root, today)
        self.auth = Auth(root, checker)
        self.dist = root / "apps" / "web-ui" / "dist"


    def index_html(self) -> bytes | None:
        try:
            return (self.dist / "index.html").read_bytes()
        except OSError:
            return None

    def static_file(self, url_path: str) -> tuple[bytes, str, str] | None:
        """dist 裡的檔案 → (內容, Content-Type, Cache-Control)，否則 None。resolve 後確認仍在 dist 底下。"""
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


class Handler(BaseHTTPRequestHandler):
    server_version = "newsletter-web"
    timeout = 30  # 秒；送一半就不動的連線不能一直佔著 thread
    head_only = False  # HEAD：同 GET，但不送 body（監控服務常用 HEAD）

    @property
    def site(self) -> Site:
        return self.server.site  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)

    def _local(self) -> bool:
        return is_local_request(self.client_address[0], self.headers, getattr(self.server, "admin", False))

    def _send(self, status: int, body: str | bytes, ctype: str = "text/plain; charset=utf-8",
              cache: str = "no-store", headers: Mapping[str, str] | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")  # 點報告裡的外部連結不帶出報告網址
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not self.head_only:
            self.wfile.write(data)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _send_shell(self, status: int = 200) -> None:
        """前端的 index.html。沒建置過就說明怎麼建置（API 不受影響）。"""
        index = self.site.index_html()
        if index is None:
            hint = (f"<!DOCTYPE html><meta charset=\"utf-8\"><title>前端尚未建置</title>"
                    f"<p>找不到 <code>apps/web-ui/dist</code>。先建置前端：<code>{BUILD_COMMAND}</code></p>")
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, hint, "text/html; charset=utf-8")
            return
        self._send(status, index, "text/html; charset=utf-8", "no-cache", {"Content-Security-Policy": CSP})

    def _denied(self) -> bool:
        """Access 驗證沒過就回 403。本機（容器的 healthcheck、管理 port）不驗。"""
        access = self.site.access
        if access is None or self._local() or access.ok(self.headers.get(ACCESS_HEADER)):
            return False
        self._send(HTTPStatus.FORBIDDEN, "forbidden")
        return True

    def do_HEAD(self) -> None:
        self.head_only = True
        self.do_GET()

    def do_GET(self) -> None:
        if self._denied():
            return
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/"):
            self._get_api(path)
        elif SPA_ROUTES.fullmatch(path):
            self._send_shell(404 if path == "/auth" and not self._local() else 200)
        elif (found := self.site.static_file(path)) is not None:
            body, ctype, cache = found
            self._send(200, body, ctype, cache)
        elif "." in path.rsplit("/", 1)[-1]:  # 像檔案的路徑不回 HTML
            self._send(404, "not found")
        else:
            self._send_shell(404)

    def _api_payload(self, path: str):
        fixed = {
            "/api/session": lambda: {"local": self._local()},
            "/api/heartbeat": lambda: {"ok": True},
            "/api/today": self.site.reports.today_payload,
            "/api/reports": self.site.reports.list_payload,
            "/api/auth-health": self.site.auth.health,
            "/api/feedback-stats": self.site.reports.feedback_stats,
        }
        if path in fixed:
            return fixed[path]()
        if m := re.fullmatch(r"/api/reports/(\d{4}-\d{2}-\d{2})", path):
            return self.site.reports.report_payload(m[1])
        if m := re.fullmatch(r"/api/feedback/([0-9a-f]{16})", path):
            return self.site.reports.feedback_target(m[1])
        if path == "/api/search":
            return self.site.reports.search(parse_qs(urlsplit(self.path).query).get("q", [""])[0])
        if path == "/api/auth" and self._local():
            return self.site.auth.status()
        raise NotFound(path)

    def _get_api(self, path: str) -> None:
        try:
            payload = self._api_payload(path)
            if not ETAG_PATHS.fullmatch(path):
                self._send_json(200, payload)
                return
            # 晨報內容＋標記都在 payload 裡，對最後的 JSON 取雜湊才不會漏掉標記變動
            body = json.dumps(payload, ensure_ascii=False)
            etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'
            if etag_matches(self.headers.get("If-None-Match", ""), etag):
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self._send(200, body, "application/json; charset=utf-8", headers={"ETag": etag})
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

    def _post_payload(self, path: str, data) -> dict:
        if path in ("/api/feedback", "/api/kg-flag"):
            field = "mark" if path == "/api/feedback" else "flag"
            if not (isinstance(data, dict) and isinstance(data.get("uid"), str) and isinstance(data.get(field), str)):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"需要字串欄位 uid、{field}")
            record = self.site.reports.record if field == "mark" else self.site.reports.record_flag
            row = record(data["uid"], data[field])
            return {"uid": row["uid"], field: row[field]}
        if path == "/api/auth/token":
            if not (isinstance(data, dict) and isinstance(data.get("token"), str)):
                raise ApiError(HTTPStatus.BAD_REQUEST, "需要字串欄位 token")
            return self.site.auth.submit_token(data["token"])
        if path == "/api/auth/test":
            return self.site.auth.test_token()
        return self.site.auth.revoke_token()  # /api/auth/revoke

    def do_POST(self) -> None:
        if self._denied():
            return
        path = urlsplit(self.path).path.rstrip("/")
        local_only = POST_ROUTES.get(path)
        if local_only is None or (local_only and not self._local()):
            self._send_json(404, {"error": "not found"})
            return
        try:
            self._send_json(200, self._post_payload(path, self._read_json()))
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "server error"})


def make_server(site: Site, host: str, port: int, quiet: bool = False, admin: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.site = site  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    server.admin = admin  # type: ignore[attr-defined]
    return server


@contextlib.contextmanager
def served(site: Site, admin: bool = False) -> Iterator[Callable[..., tuple[int, dict[str, str], str]]]:
    """selftest 用的真 HTTP 伺服器。用 http.client：路徑原樣送出（不正規化 `..`），標頭可自訂。"""
    import http.client

    server = make_server(site, DEFAULT_HOST, 0, quiet=True, admin=admin)
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
