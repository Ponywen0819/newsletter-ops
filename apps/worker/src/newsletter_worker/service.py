#!/usr/bin/env python3
"""排程服務：每天 NEWSLETTER_RUN_AT 跑一次 run_pipeline，啟動時補跑今天錯過的；GET /status、POST /run 只給 compose 內網。
另外每天 NEWSLETTER_AUTH_CHECK_AT 驗一次 Claude 認證，結果寫 state/auth_health.json 給 web 顯示。

用法：uv run --project apps/worker worker [--host 0.0.0.0] [--port 8790]
      uv run --project apps/worker worker --once [newsletter-collect 的參數，如 --lookback 72]
      uv run --project apps/worker worker --selftest

要不要跑只看 state/last_run.json（run_pipeline 寫的，手動跑的也算）和 pipeline 的鎖：
今天成功就等明天；失敗（認證、額度、被停止以外）過 RETRY_AFTER 自動重跑一次，trigger 記 retry，重跑也失敗就等明天。
認證的驗證只看 auth_health.json 的時間：今天排定時間之後驗過（web 的 /auth 測試也算）就不再驗。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_shared import ROOT, auth_health, lock, tz

from . import daily

DEFAULT_PORT = 8790
CATCHUP_AFTER = timedelta(minutes=5)  # 晚於排定時間這麼久才開始＝補跑
TICK = 30  # 秒；每次醒來重讀牆上時鐘，不怕主機睡眠、校時
TAIL = 20  # 失敗時印出本次 log 的最後幾行
HHMM_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
AUTH_EXIT = {5: "auth", 6: "quota"}  # newsletter-write 的授權失敗／額度用完（其他步驟不用 5、6）
RETRY_AFTER = timedelta(minutes=30)  # 暫時性失敗（網路、來源掛掉）等這麼久重跑一次
NO_RETRY = {0, daily.EXIT_STOPPED, *AUTH_EXIT}  # 重跑也沒用、或是人要它停的


def parse_hhmm(value: str) -> time:
    if not (m := HHMM_RE.fullmatch(value)):
        raise ValueError(f"要是 HH:MM，收到「{value}」")
    return time(int(m[1]), int(m[2]))


def next_run_at(now: datetime, at: time, tz: ZoneInfo) -> datetime:
    """now 之後（不含）的下一個 at。"""
    local = now.astimezone(tz)
    run = datetime.combine(local.date(), at, tz)
    return run if run > local else datetime.combine(local.date() + timedelta(days=1), at, tz)


def due(now: datetime, record: dict | None, lock_held: bool, at: time, tz: ZoneInfo) -> str | None:
    """現在該做什麼：None＝等下次、"busy"＝別條正在跑、"schedule"／"catchup"／"retry"＝現在跑（值就是 trigger）。"""
    local = now.astimezone(tz)
    scheduled = datetime.combine(local.date(), at, tz)
    if local < scheduled:
        return None
    if record is not None and record.get("date") == f"{local:%Y-%m-%d}" and record.get("finished_at"):
        if record.get("exit_code") in NO_RETRY or record.get("trigger") == "retry":
            return None  # 成功、不該重跑、或已經重跑過：等明天
        if now - datetime.fromisoformat(record["finished_at"]) < RETRY_AFTER:
            return None
        return "busy" if lock_held else "retry"
    if lock_held:
        return "busy"  # 包括「今天開始了還沒結束」：鎖被握著＝還在跑；鎖空著＝被打斷，下面重跑
    return "catchup" if local - scheduled > CATCHUP_AFTER else "schedule"


def check_due(now: datetime, last: dict | None, at: time, tz: ZoneInfo) -> bool:
    """今天排定時間到了、而且那之後還沒驗過。last 是 auth_health.read() 的結果。"""
    local = now.astimezone(tz)
    scheduled = datetime.combine(local.date(), at, tz)
    return local >= scheduled and (last is None or last["at"] < scheduled)


def lock_held() -> bool:
    try:
        with lock.locked(daily.LOCK, timeout=0):
            return False
    except lock.LockBusy:
        return True


def say(message: str) -> None:
    print(f"[worker] {datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


class Service:
    def __init__(self, at: time, tz: ZoneInfo, check_at: time, log_dir: Path = ROOT / "logs",
                 health: Path | None = None):
        self.at, self.tz, self.check_at, self.log_dir = at, tz, check_at, log_dir
        self.health = health or auth_health.health_path()
        self.checker = auth_health.run_check
        self.stop = threading.Event()
        self.current: dict | None = None  # {trigger, started_at}
        self._mu = threading.Lock()
        self._worker: threading.Thread | None = None

    def start(self, trigger: str) -> dict | None:
        """背景跑一次；已經在跑（這個服務或別的程序）就回 None。"""
        with self._mu:
            if self.current is not None or lock_held():
                return None
            self.current = {"trigger": trigger, "started_at": daily.now_iso()}
            self._worker = threading.Thread(target=self._work, args=(trigger,))
            self._worker.start()
            return self.current

    def _work(self, trigger: str) -> None:
        say(f"開始（{trigger}）")
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"{datetime.now():%Y-%m}.log"  # 月份以開始時間為準
            with path.open("a+", encoding="utf-8") as log:
                start = log.tell()
                code = daily.run_pipeline([], log, trigger=trigger, stop=self.stop)
                log.seek(start)
                tail = log.read().splitlines()[-TAIL:]
            say(f"結束，exit {code}")
            if code in AUTH_EXIT:
                auth_health.write(False, AUTH_EXIT[code], self.health)
            if code not in (0, daily.EXIT_BUSY):
                print("\n".join(tail), flush=True)
        finally:
            with self._mu:
                self.current = None

    def status(self) -> dict:
        nxt = next_run_at(datetime.now(timezone.utc), self.at, self.tz)
        current = self.current
        return {"state": "running" if current else "idle", "next_run_at": nxt.isoformat(),
                "current": current, "last": daily.read_record()}

    def check_auth(self) -> None:
        result = self.checker()
        auth_health.write(bool(result.get("ok")), str(result.get("kind", "other")), self.health)
        say(f"認證驗證：{result.get('kind')}")

    def tick(self, now: datetime) -> None:
        if check_due(now, auth_health.read(self.health), self.check_at, self.tz):
            self.check_auth()  # 同步跑：同一輪要開始的 pipeline 排在它後面
        if self.current is None and (action := due(now, daily.read_record(), lock_held(), self.at, self.tz)) not in (None, "busy"):
            self.start(action)

    def loop(self) -> None:
        say(f"下次執行：{next_run_at(datetime.now(timezone.utc), self.at, self.tz):%Y-%m-%d %H:%M %Z}")
        while not self.stop.is_set():
            self.tick(datetime.now(timezone.utc))
            self.stop.wait(TICK)

    def shutdown(self) -> None:
        """送停止給執行中的子程序並等它收尾（被打斷的紀錄由 run_pipeline 寫）。"""
        self.stop.set()
        if self._worker is not None:
            self._worker.join()


class Handler(BaseHTTPRequestHandler):
    server_version = "worker"

    def log_message(self, format: str, *args) -> None:
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/status":
            self._send(200, self.server.service.status())  # type: ignore[attr-defined]
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, {"error": "not found"})
        elif (started := self.server.service.start("manual")) is None:  # type: ignore[attr-defined]
            self._send(409, {"error": "pipeline 正在跑"})
        else:
            self._send(202, {"started_at": started["started_at"]})


def make_server(service: Service, host: str, port: int, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.service = service  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="worker 常駐服務：每日排程與內網 API")
    ap.add_argument("--host", default="0.0.0.0", help="容器內綁全部介面；compose 不 publish 這個 port")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NEWSLETTER_WORKER_PORT", DEFAULT_PORT)))
    ap.add_argument("--once", action="store_true", help="跑一次每日流程就結束，輸出印在終端機；其餘參數轉給 newsletter-collect")
    ap.add_argument("--selftest", action="store_true")
    args, rest = ap.parse_known_args(argv)
    if args.selftest:
        from .service_selftest import selftest

        daily.selftest()
        selftest()
        return 0
    if args.once:
        return daily.run_pipeline(rest, sys.stderr)
    if rest:
        ap.error(f"不認得的參數：{' '.join(rest)}（只有 --once 會把參數轉給 newsletter-collect）")
    times = {}
    for name, default in (("NEWSLETTER_RUN_AT", "08:00"), ("NEWSLETTER_AUTH_CHECK_AT", "07:30")):
        try:
            times[name] = parse_hhmm(os.environ.get(name, default))
        except ValueError as exc:
            print(f"[worker] {name} {exc}", file=sys.stderr)
            return 2
    service = Service(times["NEWSLETTER_RUN_AT"], tz(), times["NEWSLETTER_AUTH_CHECK_AT"])
    server = make_server(service, args.host, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop.set())
    say(f"http://{args.host}:{args.port}/status")
    service.loop()
    say("收到停止，等執行中的 pipeline 收尾")
    service.shutdown()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
