#!/usr/bin/env python3
"""排程服務：每天 NEWSLETTER_RUN_AT 跑一次 run_pipeline，啟動時補跑今天錯過的；GET /status、POST /run 只給 compose 內網。
另外每天 NEWSLETTER_AUTH_CHECK_AT 驗一次 Claude 認證，結果寫 state/auth_health.json 給 web 顯示。

用法：uv run --project apps/worker worker [--host 0.0.0.0] [--port 8790]
      uv run --project apps/worker worker --once [newsletter-collect 的參數，如 --lookback 72]
      uv run --project apps/worker worker --selftest

要不要跑只看 state/last_run.json（run_pipeline 寫的，手動跑的也算）和 pipeline 的鎖：
今天成功就等明天；失敗（認證、額度、被停止以外）過 RETRY_AFTER 從失敗的那步自動重跑一次，trigger 記 retry，重跑也失敗就等明天。
認證的驗證只看 auth_health.json 的時間：今天排定時間之後驗過（web 的 /auth 測試也算）就不再驗。
token 剩 REMIND_DAYS 裡的天數時（推算的到期日），在驗證時間寄一封提醒。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from newsletter_shared import ROOT, auth_health, auth_store, lock, tz

from . import daily

DEFAULT_PORT = 8790
CATCHUP_AFTER = timedelta(minutes=5)  # 晚於排定時間這麼久才開始＝補跑
TICK = 30  # 秒；每次醒來重讀牆上時鐘，不怕主機睡眠、校時
TAIL = 20  # 失敗時印出本次 log 的最後幾行
HHMM_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
AUTH_EXIT = {5: "auth", 6: "quota"}  # newsletter-write 的授權失敗／額度用完（其他步驟不用 5、6）
RETRY_AFTER = timedelta(minutes=30)  # 暫時性失敗（網路、來源掛掉）等這麼久重跑一次
NO_RETRY = {0, daily.EXIT_STOPPED, *AUTH_EXIT}  # 重跑也沒用、或是人要它停的
REMIND_DAYS = {30, 14, 7, 3, 1, 0, -1}  # token 剩這幾天時寄提醒；-1＝過期後第一天


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
    print(f"[worker] {datetime.now(daily.TZ):%Y-%m-%d %H:%M:%S} {message}", flush=True)


STEP_LABEL = {"collect": "蒐集新聞", "write": "撰寫報告", "render": "排版", "send": "寄信"}
EXIT_HINT = {5: "Claude 授權失效：到 /auth 重新貼上 `claude setup-token` 的 token", 6: "訂閱額度用完：等額度恢復",
             daily.EXIT_TIMEOUT: "超過時限被停掉"}


RUN_COMMAND = ("docker compose exec worker python -c \"import urllib.request as u; "
               "print(u.urlopen(u.Request('http://127.0.0.1:8790/run{query}', method='POST')).read().decode())\"")


def failure_notice(code: int, record: dict | None, tail: list[str], retried: bool) -> dict:
    """失敗通知信的 {subject, text}：今天不會再自動重跑時才寄。"""
    rec = record or {}
    step = STEP_LABEL.get(rec.get("step") or "", rec.get("step") or "?")
    why = EXIT_HINT.get(code, "")
    lines = [f"今天（{rec.get('date', '?')}）的晨報沒有寄出：在「{step}」這步失敗，exit {code}。"]
    if why:
        lines.append(why + "。")
    lines.append("已經自動重跑過一次，今天不會再試；修好後用 worker 的 POST /run 補跑。" if retried else
                 "這種失敗重跑也沒用，今天不會自動重跑；修好後用 worker 的 POST /run 補跑。")
    first = daily.resume_from(rec.get("step"))
    query = "" if first == "collect" else f"?from={first}"  # 前面的步驟今天已經有產出，不用重來
    lines += ["", f"Docker 部署的補跑（從「{STEP_LABEL[first]}」開始；在專案目錄執行，systemd 版見 deploy/README.md）：",
              RUN_COMMAND.format(query=query)]
    lines += ["", f"log 最後 {len(tail)} 行：", *tail]
    return {"subject": f"晨報失敗 {rec.get('date', '')}：{step}（exit {code}）", "text": "\n".join(lines) + "\n"}


def expiry_notice(st: dict) -> dict | None:
    """auth_store.status() → token 到期提醒的 {subject, text}；今天不用提醒就是 None。"""
    if st.get("state") not in ("expiring", "expired") or st.get("days_left") not in REMIND_DAYS:
        return None
    left = "已經過期" if st["state"] == "expired" else f"剩 {st['days_left']} 天"
    text = (f"Claude 的 OAuth token 預計 {st['expires_at']} 到期（{left}；依儲存時間推算）。\n"
            "到期後每天的晨報會在「撰寫報告」失敗。請在自己的電腦跑 `claude setup-token`，"
            "把新的 token 貼到 newsletter-web 的 /auth 頁面（只能從本機開）。\n")
    return {"subject": f"晨報：Claude token {left}", "text": text}


def send_notice(notice: dict) -> None:
    """另開程序寄（worker 不 import notify）；寄不出去只記 log。"""
    try:
        proc = subprocess.run([sys.executable, "-m", "newsletter_notify.send_email", "--notice"], cwd=ROOT,
                              input=json.dumps(notice, ensure_ascii=False), encoding="utf-8",
                              capture_output=True, timeout=120, check=False)
        ok = proc.returncode == 0
        detail = proc.stderr.strip().splitlines()[-1:] if not ok else []
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, detail = False, [str(exc)]
    say("已寄失敗通知" if ok else f"失敗通知寄不出去：{' '.join(detail)}")


class Service:
    def __init__(self, at: time, tz: ZoneInfo, check_at: time, log_dir: Path = ROOT / "logs",
                 health: Path | None = None):
        self.at, self.tz, self.check_at, self.log_dir = at, tz, check_at, log_dir
        self.health = health or auth_health.health_path()
        self.checker = auth_health.run_check
        self.notifier = send_notice
        self.token_status = lambda: auth_store.status(auth_store.token_path())
        self.reminded: str | None = None  # 今天檢查過到期提醒了沒（日期）；重啟後同一天可能再寄一次
        self.stop = threading.Event()
        self.current: dict | None = None  # {trigger, started_at}
        self._mu = threading.Lock()
        self._worker: threading.Thread | None = None

    def start(self, trigger: str, first: str | None = None) -> dict | None:
        """背景跑一次；已經在跑（這個服務或別的程序）就回 None。first：從哪一步開始（沒給就依 trigger 決定）。"""
        with self._mu:
            if self.current is not None or lock_held():
                return None
            self.current = {"trigger": trigger, "started_at": daily.now_iso()}
            # 自動重跑從失敗的那步開始：send 沒寄出不必再花 token 重寫報告
            if first is None:
                first = daily.resume_from((daily.read_record() or {}).get("step")) if trigger == "retry" else "collect"
            self._worker = threading.Thread(target=self._work, args=(trigger, first))
            self._worker.start()
            return self.current

    def _work(self, trigger: str, first: str = "collect") -> None:
        say(f"開始（{trigger}）" if first == "collect" else f"開始（{trigger}，從 {first} 起）")
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"{datetime.now(self.tz):%Y-%m}.log"  # 月份以開始時間為準
            with path.open("a+", encoding="utf-8") as log:
                start = log.tell()
                code = daily.run_pipeline([], log, trigger=trigger, stop=self.stop, start=first)
                log.seek(start)
                tail = log.read().splitlines()[-TAIL:]
            say(f"結束，exit {code}")
            if code in AUTH_EXIT:
                auth_health.write(False, AUTH_EXIT[code], self.health)
            if code not in (0, daily.EXIT_BUSY):
                print("\n".join(tail), flush=True)
            if code not in (0, daily.EXIT_BUSY, daily.EXIT_STOPPED) and (trigger == "retry" or code in NO_RETRY):
                self.notifier(failure_notice(code, daily.read_record(), tail, retried=trigger == "retry"))
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

    def remind(self, now: datetime) -> None:
        local = now.astimezone(self.tz)
        if self.reminded == f"{local:%Y-%m-%d}" or local.time() < self.check_at:
            return
        self.reminded = f"{local:%Y-%m-%d}"
        if (notice := expiry_notice(self.token_status())) is not None:
            say(notice["subject"])
            self.notifier(notice)

    def tick(self, now: datetime) -> None:
        self.remind(now)
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
        url = urlsplit(self.path)
        step = parse_qs(url.query).get("from", ["collect"])[0]
        if url.path != "/run":
            self._send(404, {"error": "not found"})
        elif step not in daily.STEPS:
            self._send(400, {"error": f"from 只能是 {'、'.join(daily.STEPS)}"})
        elif (started := self.server.service.start("manual", daily.resume_from(step))) is None:  # type: ignore[attr-defined]
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
