"""晨報網頁的後端入口。API 列表與安全設計見 apps/web-server/README.md。

用法：uv run --project apps/web-server newsletter-web [--host 127.0.0.1] [--port 8787] [--admin-port N] [--auth-check-interval 3600] [--selftest]
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

from .auth import CHECK_INTERVAL
from .server import BUILD_COMMAND, DEFAULT_HOST, DEFAULT_PORT, Site, make_server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="晨報瀏覽與 👍／👎 回饋")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"預設 {DEFAULT_HOST}，不要綁到對外位址（沒有登入）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NEWSLETTER_WEB_PORT", DEFAULT_PORT)))
    ap.add_argument("--admin-port", type=int, default=int(os.environ.get("NEWSLETTER_ADMIN_PORT", "0")),
                    help="另開一個管理 port（綁在 --host），/auth 與 /api/auth* 只在這裡開；0＝不開。給 Docker 用，"
                         "compose 只把它 publish 成 127.0.0.1:<port>（見 deploy/README.md）")
    ap.add_argument("--auth-check-interval", type=int, metavar="SECONDS",
                    default=int(os.environ.get("NEWSLETTER_AUTH_CHECK_INTERVAL", CHECK_INTERVAL)),
                    help="每隔幾秒背景驗證一次 Claude 認證（會實際呼叫一次）；0＝不驗證。預設 %(default)s")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from .selftest import selftest

        selftest()
        return 0
    if args.admin_port and args.admin_port == args.port:
        ap.error("--admin-port 不能和 --port 相同")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[web] 警告：綁在 {args.host}，這個服務沒有登入機制，任何連得到的人都能寫入回饋。", file=sys.stderr)
    site = Site()
    if site.index_html() is None:
        print(f"[web] 找不到 apps/web-ui/dist，頁面會回 503。先建置前端：{BUILD_COMMAND}", file=sys.stderr)
    server = make_server(site, args.host, args.port)
    print(f"[web] http://{args.host}:{server.server_address[1]}/  （Ctrl-C 結束）", file=sys.stderr)
    stop = threading.Event()
    if args.auth_check_interval > 0:
        threading.Thread(target=site.auth.monitor, args=(args.auth_check_interval, stop), daemon=True).start()
    admin = make_server(site, args.host, args.admin_port, admin=True) if args.admin_port else None
    if admin:
        threading.Thread(target=admin.serve_forever, daemon=True).start()
        print(f"[web] 管理 port http://{args.host}:{args.admin_port}/auth  （不要 publish 到 127.0.0.1 以外）", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if admin:
            admin.shutdown()
            admin.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
