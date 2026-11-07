"""worker --selftest 的服務部分：假的四步、暫存目錄，不連網、不呼叫 Claude。"""
from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time as clock
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_shared import lock

from . import daily
from .service import Service, due, make_server, next_run_at, parse_hhmm

TPE = ZoneInfo("Asia/Taipei")
AT = time(8, 0)


def tpe(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


def selftest() -> None:
    _selftest_clock()
    _selftest_due()
    with tempfile.TemporaryDirectory() as d, daily.fake_steps(Path(d)):
        _selftest_http(Path(d))
        _selftest_tick(Path(d))
    _selftest_sigterm()
    print("ok")


def _selftest_clock() -> None:
    assert parse_hhmm("08:00") == AT and parse_hhmm("23:59") == time(23, 59)
    for bad in ("8:00", "24:00", "08:60", "0800", ""):
        try:
            parse_hhmm(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert next_run_at(tpe("2026-11-03T07:59"), AT, TPE) == tpe("2026-11-03T08:00")
    assert next_run_at(tpe("2026-11-03T08:00"), AT, TPE) == tpe("2026-11-04T08:00")  # 剛好到點：已經是這一輪
    assert next_run_at(tpe("2026-11-03T23:30"), AT, TPE) == tpe("2026-11-04T08:00")
    assert next_run_at(tpe("2026-12-31T09:00"), AT, TPE) == tpe("2027-01-01T08:00")  # 跨年
    # 時區換算：UTC 23:30 是台北隔天 07:30
    utc = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    assert next_run_at(utc, AT, TPE) == tpe("2026-11-04T08:00")


def _selftest_due() -> None:
    def rec(date: str, finished: bool, code: int | None = 0) -> dict:
        return {"date": date, "trigger": "schedule", "started_at": "x",
                "finished_at": "y" if finished else None, "exit_code": code if finished else None}

    today, yesterday = "2026-11-03", "2026-11-02"
    rows = [
        # (現在, 紀錄, 鎖, 預期)
        ("07:59", None, False, None),
        ("07:59", rec(yesterday, False), False, None),                 # 還沒到點：什麼都不管
        ("08:00", None, False, "schedule"),
        ("08:05", None, False, "schedule"),
        ("08:06", None, False, "catchup"),                            # 晚了超過 5 分鐘
        ("10:00", rec(today, True), False, None),                     # 今天成功
        ("10:00", rec(today, True, 1), False, None),                  # 今天失敗：不自動重跑
        ("10:00", rec(today, False), False, "catchup"),               # 開始沒結束、鎖空著：被打斷，重跑
        ("10:00", rec(today, False), True, "busy"),                   # 鎖被握著：還在跑
        ("10:00", rec(yesterday, True), False, "catchup"),            # 昨天的紀錄＝今天沒有
        ("10:00", None, True, "busy"),
        ("23:59", rec(yesterday, True), False, "catchup"),
    ]
    for hhmm, record, held, want in rows:
        got = due(tpe(f"{today}T{hhmm}"), record, held, AT, TPE)
        assert got == want, (hhmm, record, held, got, want)
    # 紀錄的日期是台北的日期：UTC 前一天 23:00＝台北 07:00，還沒到點
    assert due(datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc), None, False, AT, TPE) is None


def _wait(cond, seconds: float = 15) -> None:
    deadline = clock.monotonic() + seconds
    while not cond():
        assert clock.monotonic() < deadline, "等太久"
        clock.sleep(0.05)


def _selftest_http(tmp: Path) -> None:
    service = Service(AT, TPE, tmp / "logs")
    server = make_server(service, "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method: str, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    try:
        status, st = req("GET", "/status")
        assert status == 200 and st["state"] == "idle" and st["current"] is None and st["last"] is None, st
        assert datetime.fromisoformat(st["next_run_at"]) > datetime.now(timezone.utc)
        assert req("GET", "/nope")[0] == 404 and req("POST", "/status")[0] == 404

        # 成功的一次：202 → running → idle，紀錄是 manual／0，輸出進當月 log
        status, body = req("POST", "/run")
        assert status == 202 and body["started_at"], body
        _wait(lambda: req("GET", "/status")[1]["state"] == "idle")
        last = req("GET", "/status")[1]["last"]
        assert last["trigger"] == "manual" and last["exit_code"] == 0, last
        assert "完成" in (tmp / "logs" / f"{datetime.now():%Y-%m}.log").read_text(encoding="utf-8")

        # 執行中：再送是 409；/status 看得到 current；停止後紀錄是被打斷（143）
        os.environ["FAKE_FAIL"] = "agent:hang"
        for name in daily.MODULES:
            (tmp / name).unlink(missing_ok=True)
        assert req("POST", "/run")[0] == 202
        _wait((tmp / "agent").exists)
        assert req("POST", "/run")[0] == 409
        st = req("GET", "/status")[1]
        assert st["state"] == "running" and st["current"]["trigger"] == "manual", st
        service.shutdown()
        assert not (tmp / "render").exists()
        st = req("GET", "/status")[1]
        assert st["state"] == "idle" and st["last"]["exit_code"] == daily.EXIT_STOPPED and st["last"]["finished_at"], st
        os.environ["FAKE_FAIL"] = ""
        service.stop.clear()

        # 鎖被別的程序握著：不執行、409，紀錄不變（不會顯示成功）
        before = req("GET", "/status")[1]["last"]
        holding, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.locked(daily.LOCK):
                holding.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        assert holding.wait(5)
        try:
            assert req("POST", "/run")[0] == 409
            assert req("GET", "/status")[1] == {**req("GET", "/status")[1], "state": "idle", "last": before}
        finally:
            release.set()
            t.join()
    finally:
        server.shutdown()
        server.server_close()


def _selftest_tick(tmp: Path) -> None:
    # 今天的紀錄開始了沒結束、鎖空著（被打斷）：tick 重跑；跑完之後同一天不再跑
    service = Service(time(0, 0), TPE, tmp / "logs")
    daily.RECORD.write_text(json.dumps({"date": f"{datetime.now(TPE):%Y-%m-%d}", "finished_at": None}))
    service.tick(datetime.now(timezone.utc))
    worker = service._worker
    assert service.current is not None and worker is not None
    worker.join(15)
    rec = daily.read_record()
    assert rec["exit_code"] == 0 and rec["trigger"] in ("catchup", "schedule"), rec
    service.tick(datetime.now(timezone.utc))
    assert service.current is None


# 真的程序收 SIGTERM：子程序被終止、紀錄寫好、期限內離開
SIGTERM_CHILD = """import sys
from pathlib import Path
from newsletter_worker import daily, service
with daily.fake_steps(Path(sys.argv[1])):
    import os
    os.environ.update(FAKE_FAIL="agent:hang", NEWSLETTER_RUN_AT="00:00")
    sys.exit(service.main(["--host", "127.0.0.1", "--port", "0"]))
"""


def _selftest_sigterm() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proc = subprocess.Popen([sys.executable, "-c", SIGTERM_CHILD, d], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env={**os.environ, "NEWSLETTER_ROOT": d})
        try:
            _wait((tmp / "agent").exists)  # 啟動就補跑（00:00 早就過了），卡在 agent
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=15)[0]
        finally:
            if proc.poll() is None:
                proc.kill()
        assert proc.returncode == 0, out
        rec = json.loads((tmp / "state" / "last_run.json").read_text(encoding="utf-8"))
        assert rec["exit_code"] == daily.EXIT_STOPPED and rec["finished_at"] and rec["trigger"] in ("catchup", "schedule"), rec
        assert "收到停止" in out and f"exit {daily.EXIT_STOPPED}" in out, out
        assert not (tmp / "render").exists()
