"""測試用的真 HTTP 伺服器。"""
from __future__ import annotations

import contextlib
import http.client
import json
import threading
from typing import Callable, Iterator, Mapping

from newsletter_web_server.server import DEFAULT_HOST, Site, make_server


@contextlib.contextmanager
def served(site: Site, admin: bool = False) -> Iterator[Callable[..., tuple[int, dict[str, str], str]]]:
    """真的 HTTP 伺服器。用 http.client：路徑原樣送出（不正規化 `..`），標頭可自訂。"""
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
