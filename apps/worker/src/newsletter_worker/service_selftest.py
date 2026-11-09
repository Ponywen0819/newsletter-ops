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

from newsletter_shared import auth_health, lock

from . import daily
from .service import Service, check_due, due, expiry_notice, failure_notice, make_server, next_run_at, parse_hhmm

TPE = ZoneInfo("Asia/Taipei")
AT = time(8, 0)
CHECK_AT = time(7, 30)


def service_for(tmp: Path, at: time, check_at: time = CHECK_AT) -> Service:
    """假的認證驗證：記下呼叫次數，不跑子程序。"""
    service = Service(at, TPE, check_at, tmp / "logs", tmp / "state" / "auth_health.json")
    service.checks = []  # type: ignore[attr-defined]
    service.checker = lambda: service.checks.append(1) or {"ok": True, "kind": "ok", "message": ""}  # type: ignore[attr-defined]
    service.notices = []  # type: ignore[attr-defined]
    service.notifier = service.notices.append  # type: ignore[attr-defined]
    service.beats = []  # type: ignore[attr-defined]
    service.beat = service.beats.append  # type: ignore[attr-defined]
    service.token_status = lambda: {"state": "ok", "days_left": 200}
    return service


def tpe(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


def _selftest_heartbeat() -> None:
    from unittest import mock

    from . import service as svc
    hits: list[str] = []
    with mock.patch.object(svc.urllib.request, "urlopen", lambda url, timeout: hits.append(url) or mock.MagicMock()):
        with mock.patch.dict(os.environ, {svc.HEARTBEAT_ENV: ""}):
            svc.heartbeat(True)
        with mock.patch.dict(os.environ, {svc.HEARTBEAT_ENV: "https://hc.example/abc/"}):
            svc.heartbeat(True)
            svc.heartbeat(False)
    assert hits == ["https://hc.example/abc", "https://hc.example/abc/fail"], hits
    with mock.patch.dict(os.environ, {svc.HEARTBEAT_ENV: "http://127.0.0.1:9/x"}):
        svc.heartbeat(True)  # 連不到：只記 log，不丟例外


def selftest() -> None:
    _selftest_heartbeat()
    _selftest_clock()
    _selftest_due()
    _selftest_check_due()
    _selftest_notice()
    _selftest_expiry()
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
    def rec(date: str, finished: bool, code: int | None = 0, trigger: str = "schedule", at: str = "08:10") -> dict:
        end = tpe(f"{date}T{at}").astimezone(timezone.utc).isoformat(timespec="seconds")
        return {"date": date, "trigger": trigger, "started_at": "x",
                "finished_at": end if finished else None, "exit_code": code if finished else None}

    today, yesterday = "2026-11-03", "2026-11-02"
    rows = [
        # (現在, 紀錄, 鎖, 預期)
        ("07:59", None, False, None),
        ("07:59", rec(yesterday, False), False, None),                 # 還沒到點：什麼都不管
        ("08:00", None, False, "schedule"),
        ("08:05", None, False, "schedule"),
        ("08:06", None, False, "catchup"),                            # 晚了超過 5 分鐘
        ("10:00", rec(today, True), False, None),                     # 今天成功
        ("08:39", rec(today, True, 1), False, None),                  # 今天失敗：30 分鐘內先等
        ("08:40", rec(today, True, 1), False, "retry"),               # 之後重跑一次
        ("08:40", rec(today, True, 1), True, "busy"),
        ("10:00", rec(today, True, 1, "retry"), False, None),         # 重跑過了還失敗：等明天
        ("10:00", rec(today, True, 3, "manual"), False, "retry"),     # 手動跑的失敗也重跑一次
        ("10:00", rec(today, True, 5), False, None),                  # 認證失效、額度用完、被停止：不重跑
        ("10:00", rec(today, True, 6), False, None),
        ("10:00", rec(today, True, 143), False, None),
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


def _selftest_check_due() -> None:
    def last(s: str) -> dict:
        return {"ok": True, "kind": "ok", "at": tpe(s)}

    assert not check_due(tpe("2026-11-03T07:29"), None, CHECK_AT, TPE)
    assert check_due(tpe("2026-11-03T07:30"), None, CHECK_AT, TPE)
    assert check_due(tpe("2026-11-03T09:00"), last("2026-11-02T07:30"), CHECK_AT, TPE)  # 昨天驗的：啟動晚了也補一次
    assert check_due(tpe("2026-11-03T09:00"), last("2026-11-03T07:00"), CHECK_AT, TPE)  # 今天排定時間之前驗的不算
    assert not check_due(tpe("2026-11-03T09:00"), last("2026-11-03T07:30"), CHECK_AT, TPE)
    assert not check_due(tpe("2026-11-03T09:00"), last("2026-11-03T08:15"), CHECK_AT, TPE)  # web 的 /auth 測試也算
    utc = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)  # 台北 11/3 07:30
    assert check_due(utc, last("2026-11-02T07:30"), CHECK_AT, TPE)


def _selftest_notice() -> None:
    rec = {"date": "2026-11-03", "step": "write"}
    n = failure_notice(5, rec, ["line a", "line b"], retried=False)
    assert n["subject"] == "晨報失敗 2026-11-03：撰寫報告（exit 5）", n
    assert "/auth" in n["text"] and "重跑也沒用" in n["text"] and n["text"].endswith("line a\nline b\n"), n
    assert "8790/run?from=write'" in n["text"] and n["text"].index("8790/run") < n["text"].index("line a"), n  # 補跑指令在 log 前面
    assert "8790/run'" in failure_notice(1, {"step": "collect"}, [], retried=True)["text"]
    assert "8790/run?from=render'" in failure_notice(1, {"step": "send"}, [], retried=True)["text"]
    n = failure_notice(3, {"date": "2026-11-03", "step": "collect"}, [], retried=True)
    assert "蒐集新聞" in n["subject"] and "已經自動重跑過一次" in n["text"], n
    assert "?" in failure_notice(1, None, [], retried=True)["subject"]


def _selftest_expiry() -> None:
    st = {"state": "expiring", "days_left": 7, "expires_at": "2027-01-01"}
    n = expiry_notice(st)
    assert n is not None and "剩 7 天" in n["subject"] and "2027-01-01" in n["text"] and "/auth" in n["text"], n
    assert expiry_notice({**st, "days_left": 6}) is None  # 只在固定幾天提醒，不要天天寄
    assert expiry_notice({"state": "ok", "days_left": 30}) is None and expiry_notice({"state": "missing"}) is None
    assert "已經過期" in expiry_notice({"state": "expired", "days_left": -1, "expires_at": "x"})["subject"]
    assert expiry_notice({"state": "expired", "days_left": -5, "expires_at": "x"}) is None

    # 驗證時間之後每天檢查一次：同一天不重寄，還沒到時間不檢查
    with tempfile.TemporaryDirectory() as d:
        service = service_for(Path(d), AT)
        service.token_status = lambda: st
        service.remind(tpe("2026-11-08T07:00"))
        assert service.notices == []  # type: ignore[attr-defined]
        service.remind(tpe("2026-11-08T07:31"))
        service.remind(tpe("2026-11-08T12:00"))
        assert len(service.notices) == 1  # type: ignore[attr-defined]
        service.remind(tpe("2026-11-09T07:31"))
        assert len(service.notices) == 2  # type: ignore[attr-defined]


def _wait(cond, seconds: float = 15) -> None:
    deadline = clock.monotonic() + seconds
    while not cond():
        assert clock.monotonic() < deadline, "等太久"
        clock.sleep(0.05)


def _selftest_http(tmp: Path) -> None:
    service = service_for(tmp, AT)
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

        # ?from=：從指定的步驟開始（send 要 render 的輸出，從 render 起）；不認得的步驟 400
        for name in daily.MODULES:
            (tmp / name).unlink(missing_ok=True)
        assert req("POST", "/run?from=send")[0] == 202
        _wait(lambda: req("GET", "/status")[1]["state"] == "idle")
        assert not (tmp / "write").exists() and (tmp / "render").exists() and (tmp / "send").exists()
        assert req("GET", "/status")[1]["last"]["exit_code"] == 0
        assert req("POST", "/run?from=nope")[0] == 400

        # 執行中：再送是 409；/status 看得到 current；停止後紀錄是被打斷（143）
        os.environ["FAKE_FAIL"] = "write:hang"
        for name in daily.MODULES:
            (tmp / name).unlink(missing_ok=True)
        assert req("POST", "/run")[0] == 202
        _wait((tmp / "write").exists)
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
    # 認證的驗證排在同一輪的 pipeline 前面，結果寫進 auth_health.json；同一天不再驗
    service = service_for(tmp, time(0, 0), time(0, 0))
    daily.RECORD.write_text(json.dumps({"date": f"{datetime.now(TPE):%Y-%m-%d}", "finished_at": None}))
    service.tick(datetime.now(timezone.utc))
    worker = service._worker
    assert service.current is not None and worker is not None
    assert service.checks == [1] and auth_health.read(service.health)["kind"] == "ok"  # type: ignore[attr-defined]
    worker.join(15)
    rec = daily.read_record()
    assert rec["exit_code"] == 0 and rec["trigger"] in ("catchup", "schedule"), rec
    service.tick(datetime.now(timezone.utc))
    assert service.current is None and service.checks == [1]  # type: ignore[attr-defined]

    # 寫報告授權失敗（exit 5）／額度用完（6）：順手更新驗證結果；其他失敗不動它
    for fail, want in (("write:5", "auth"), ("write:6", "quota"), ("collect:3", "quota")):
        os.environ["FAKE_FAIL"] = fail
        daily.RECORD.unlink()
        service.tick(datetime.now(timezone.utc))
        service._worker.join(15)
        got = auth_health.read(service.health)
        assert got["ok"] is False and got["kind"] == want, (fail, got)
    os.environ["FAKE_FAIL"] = ""
    # 失敗通知：5、6 今天不會再重跑 → 各寄一封；collect:3 之後還會重跑 → 不寄
    subjects = [n["subject"] for n in service.notices]  # type: ignore[attr-defined]
    assert len(subjects) == 2 and "exit 5" in subjects[0] and "exit 6" in subjects[1], subjects

    # 自動重跑：寄信失敗的話只重跑 render、send，不再呼叫 writer
    long_ago = "2000-01-01T00:00:00+00:00"
    daily.RECORD.write_text(json.dumps({"date": f"{datetime.now(TPE):%Y-%m-%d}", "trigger": "schedule",
                                        "finished_at": long_ago, "exit_code": 1, "step": "send"}))
    for name in daily.MODULES:
        (tmp / name).unlink(missing_ok=True)
    service.tick(datetime.now(timezone.utc))
    service._worker.join(15)
    rec = daily.read_record()
    assert rec["trigger"] == "retry" and rec["exit_code"] == 0, rec
    assert not (tmp / "collect").exists() and not (tmp / "write").exists() and (tmp / "send").exists()
    assert len(service.notices) == 2  # type: ignore[attr-defined]  # 重跑成功：不寄

    # 重跑也失敗：寄一封，說已經重跑過
    daily.RECORD.write_text(json.dumps({"date": f"{datetime.now(TPE):%Y-%m-%d}", "trigger": "schedule",
                                        "finished_at": long_ago, "exit_code": 1, "step": "send"}))
    os.environ["FAKE_FAIL"] = "send:1"
    service.tick(datetime.now(timezone.utc))
    service._worker.join(15)
    os.environ["FAKE_FAIL"] = ""
    assert daily.read_record()["trigger"] == "retry" and "已經自動重跑過一次" in service.notices[-1]["text"]  # type: ignore[attr-defined]
    assert len(service.notices) == 3  # type: ignore[attr-defined]
    # heartbeat：成功一次一個 True；寄失敗通知時（最終失敗）一個 False；之後還會重跑的不送
    assert service.beats == [True, False, False, True, False], service.beats  # type: ignore[attr-defined]


# 真的程序收 SIGTERM：子程序被終止、紀錄寫好、期限內離開
SIGTERM_CHILD = """import sys
from pathlib import Path
from newsletter_worker import daily, service
service.auth_health.run_check = lambda: {"ok": True, "kind": "ok", "message": ""}  # 不跑真的驗證
with daily.fake_steps(Path(sys.argv[1])):
    import os
    os.environ.update(FAKE_FAIL="write:hang", NEWSLETTER_RUN_AT="00:00")
    sys.exit(service.main(["--host", "127.0.0.1", "--port", "0"]))
"""


def _selftest_sigterm() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proc = subprocess.Popen([sys.executable, "-c", SIGTERM_CHILD, d], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env={**os.environ, "NEWSLETTER_ROOT": d})
        try:
            _wait((tmp / "write").exists)  # 啟動就補跑（00:00 早就過了），卡在 write
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
