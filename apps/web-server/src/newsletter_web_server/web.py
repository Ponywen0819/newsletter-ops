"""晨報網頁的後端入口。API 列表與安全設計見 apps/web-server/README.md。

用法：uv run --project apps/web-server newsletter-web [--host 127.0.0.1] [--port 8787]
"""
from __future__ import annotations

import argparse
import os
import sys

from .server import BUILD_COMMAND, DEFAULT_HOST, DEFAULT_PORT, Site, make_server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="晨報瀏覽與 👍／👎 回饋")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"預設 {DEFAULT_HOST}；沒有登入，對外要放在 nginx 後面（見 deploy/README.md）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NEWSLETTER_WEB_PORT", DEFAULT_PORT)))
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[web] 綁在 {args.host}：這個服務不做身分驗證，連得到的人都能讀寫、也能開 /auth。", file=sys.stderr)
    site = Site()
    if site.index_html() is None:
        print(f"[web] 找不到 apps/web-ui/dist，頁面會回 503。先建置前端：{BUILD_COMMAND}", file=sys.stderr)
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
    raise SystemExit(main())
