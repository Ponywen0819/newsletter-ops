import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from unittest import mock

import pytest
from helpers import AT, CHECK_AT, TPE, reset, service_for, tpe, wait

from newsletter_shared import auth_health, lock
from newsletter_worker import daily
from newsletter_worker import service as svc
from newsletter_worker.service import check_due, due, expiry_notice, failure_notice, make_server, next_run_at, parse_hhmm


def test_heartbeat(monkeypatch):
    hits: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: hits.append(url) or mock.MagicMock())
    monkeypatch.setenv(svc.HEARTBEAT_ENV, "")
    svc.heartbeat(True)
    monkeypatch.setenv(svc.HEARTBEAT_ENV, "https://hc.example/abc/")
    svc.heartbeat(True)
    svc.heartbeat(False)
    assert hits == ["https://hc.example/abc", "https://hc.example/abc/fail"]


def test_heartbeat_unreachable(monkeypatch):
    monkeypatch.setenv(svc.HEARTBEAT_ENV, "http://127.0.0.1:9/x")
    svc.heartbeat(True)  # 連不到：只記 log，不丟例外


def test_parse_hhmm():
    assert parse_hhmm("08:00") == AT and parse_hhmm("23:59") == time(23, 59)


@pytest.mark.parametrize("bad", ["8:00", "24:00", "08:60", "0800", ""])
def test_parse_hhmm_bad(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_next_run_at():
    assert next_run_at(tpe("2026-11-03T07:59"), AT, TPE) == tpe("2026-11-03T08:00")
    assert next_run_at(tpe("2026-11-03T08:00"), AT, TPE) == tpe("2026-11-04T08:00")  # 剛好到點：已經是這一輪
    assert next_run_at(tpe("2026-11-03T23:30"), AT, TPE) == tpe("2026-11-04T08:00")
    assert next_run_at(tpe("2026-12-31T09:00"), AT, TPE) == tpe("2027-01-01T08:00")  # 跨年
    utc = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)  # UTC 23:30 是台北隔天 07:30
    assert next_run_at(utc, AT, TPE) == tpe("2026-11-04T08:00")


def rec(date: str, finished: bool, code: int | None = 0, trigger: str = "schedule", at: str = "08:10") -> dict:
    end = tpe(f"{date}T{at}").astimezone(timezone.utc).isoformat(timespec="seconds")
    return {"date": date, "trigger": trigger, "started_at": "x",
            "finished_at": end if finished else None, "exit_code": code if finished else None}


TODAY, YESTERDAY = "2026-11-03", "2026-11-02"


@pytest.mark.parametrize(("hhmm", "record", "held", "want"), [
    ("07:59", None, False, None),
    ("07:59", rec(YESTERDAY, False), False, None),                 # 還沒到點：什麼都不管
    ("08:00", None, False, "schedule"),
    ("08:05", None, False, "schedule"),
    ("08:06", None, False, "catchup"),                            # 晚了超過 5 分鐘
    ("10:00", rec(TODAY, True), False, None),                     # 今天成功
    ("08:39", rec(TODAY, True, 1), False, None),                  # 今天失敗：30 分鐘內先等
    ("08:40", rec(TODAY, True, 1), False, "retry"),               # 之後重跑一次
    ("08:40", rec(TODAY, True, 1), True, "busy"),
    ("10:00", rec(TODAY, True, 1, "retry"), False, None),         # 重跑過了還失敗：等明天
    ("10:00", rec(TODAY, True, 3, "manual"), False, "retry"),     # 手動跑的失敗也重跑一次
    ("10:00", rec(TODAY, True, 5), False, None),                  # 認證失效、額度用完、被停止：不重跑
    ("10:00", rec(TODAY, True, 6), False, None),
    ("10:00", rec(TODAY, True, 143), False, None),
    ("10:00", rec(TODAY, False), False, "catchup"),               # 開始沒結束、鎖空著：被打斷，重跑
    ("10:00", rec(TODAY, False), True, "busy"),                   # 鎖被握著：還在跑
    ("10:00", rec(YESTERDAY, True), False, "catchup"),            # 昨天的紀錄＝今天沒有
    ("10:00", None, True, "busy"),
    ("23:59", rec(YESTERDAY, True), False, "catchup"),
])
def test_due(hhmm, record, held, want):
    assert due(tpe(f"{TODAY}T{hhmm}"), record, held, AT, TPE) == want


def test_due_uses_local_date():
    """紀錄的日期是台北的日期：UTC 前一天 23:00＝台北 07:00，還沒到點。"""
    assert due(datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc), None, False, AT, TPE) is None


def test_check_due():
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


def test_failure_notice():
    n = failure_notice(5, {"date": "2026-11-03", "step": "write"}, ["line a", "line b"], retried=False)
    assert n["subject"] == "晨報失敗 2026-11-03：撰寫報告（exit 5）"
    assert "/auth" in n["text"] and "重跑也沒用" in n["text"] and n["text"].endswith("line a\nline b\n")
    assert "8790/run?from=write'" in n["text"] and n["text"].index("8790/run") < n["text"].index("line a")  # 補跑指令在 log 前面
    assert "8790/run'" in failure_notice(1, {"step": "collect"}, [], retried=True)["text"]
    assert "8790/run?from=render'" in failure_notice(1, {"step": "send"}, [], retried=True)["text"]
    n = failure_notice(3, {"date": "2026-11-03", "step": "collect"}, [], retried=True)
    assert "蒐集新聞" in n["subject"] and "已經自動重跑過一次" in n["text"]
    assert "?" in failure_notice(1, None, [], retried=True)["subject"]


EXPIRING = {"state": "expiring", "days_left": 7, "expires_at": "2027-01-01"}


def test_expiry_notice():
    n = expiry_notice(EXPIRING)
    assert n is not None and "剩 7 天" in n["subject"] and "2027-01-01" in n["text"] and "/auth" in n["text"]
    assert expiry_notice({**EXPIRING, "days_left": 6}) is None  # 只在固定幾天提醒，不要天天寄
    assert expiry_notice({"state": "ok", "days_left": 30}) is None and expiry_notice({"state": "missing"}) is None
    assert "已經過期" in expiry_notice({"state": "expired", "days_left": -1, "expires_at": "x"})["subject"]
    assert expiry_notice({"state": "expired", "days_left": -5, "expires_at": "x"}) is None


def test_remind_once_a_day(tmp_path):
    """驗證時間之後每天檢查一次：同一天不重寄，還沒到時間不檢查。"""
    service = service_for(tmp_path, AT)
    service.token_status = lambda: EXPIRING
    service.remind(tpe("2026-11-08T07:00"))
    assert service.notices == []
    service.remind(tpe("2026-11-08T07:31"))
    service.remind(tpe("2026-11-08T12:00"))
    assert len(service.notices) == 1
    service.remind(tpe("2026-11-09T07:31"))
    assert len(service.notices) == 2


def test_http(steps):
    tmp = steps
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

    def idle() -> bool:
        return req("GET", "/status")[1]["state"] == "idle"

    try:
        status, st = req("GET", "/status")
        assert status == 200 and st["state"] == "idle" and st["current"] is None and st["last"] is None
        assert datetime.fromisoformat(st["next_run_at"]) > datetime.now(timezone.utc)
        assert req("GET", "/nope")[0] == 404 and req("POST", "/status")[0] == 404

        # 成功的一次：202 → running → idle，紀錄是 manual／0，輸出進當月 log
        status, body = req("POST", "/run")
        assert status == 202 and body["started_at"]
        wait(idle)
        last = req("GET", "/status")[1]["last"]
        assert last["trigger"] == "manual" and last["exit_code"] == 0
        assert "完成" in (tmp / "logs" / f"{datetime.now():%Y-%m}.log").read_text(encoding="utf-8")

        # ?from=：從指定的步驟開始（send 要 render 的輸出，從 render 起）；不認得的步驟 400
        reset(tmp)
        assert req("POST", "/run?from=send")[0] == 202
        wait(idle)
        assert not (tmp / "write").exists() and (tmp / "render").exists() and (tmp / "send").exists()
        assert req("GET", "/status")[1]["last"]["exit_code"] == 0
        assert req("POST", "/run?from=nope")[0] == 400

        # 執行中：再送是 409；/status 看得到 current；停止後紀錄是被打斷（143）
        os.environ["FAKE_FAIL"] = "write:hang"
        reset(tmp)
        assert req("POST", "/run")[0] == 202
        wait((tmp / "write").exists)
        assert req("POST", "/run")[0] == 409
        st = req("GET", "/status")[1]
        assert st["state"] == "running" and st["current"]["trigger"] == "manual"
        service.shutdown()
        assert not (tmp / "render").exists()
        st = req("GET", "/status")[1]
        assert st["state"] == "idle" and st["last"]["exit_code"] == daily.EXIT_STOPPED and st["last"]["finished_at"]
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


def tick(service) -> None:
    service.tick(datetime.now(timezone.utc))
    if service._worker:
        service._worker.join(15)


def today_record(**kw) -> None:
    daily.RECORD.parent.mkdir(parents=True, exist_ok=True)
    daily.RECORD.write_text(json.dumps({"date": f"{datetime.now(TPE):%Y-%m-%d}", **kw}))


def test_tick_catchup_and_auth_check(steps):
    """開始了沒結束、鎖空著（被打斷）：tick 重跑；跑完同一天不再跑。認證驗證排在 pipeline 前面，同一天不再驗。"""
    service = service_for(steps, time(0, 0), time(0, 0))
    today_record(finished_at=None)
    service.tick(datetime.now(timezone.utc))
    worker = service._worker
    assert service.current is not None and worker is not None
    assert service.checks == [1] and auth_health.read(service.health)["kind"] == "ok"
    worker.join(15)
    rec_ = daily.read_record()
    assert rec_["exit_code"] == 0 and rec_["trigger"] in ("catchup", "schedule")
    service.tick(datetime.now(timezone.utc))
    assert service.current is None and service.checks == [1]


def test_tick_failures_and_retry(steps):
    service = service_for(steps, time(0, 0), time(0, 0))
    tick(service)  # 先驗過今天的認證、跑過一次

    # 寫報告授權失敗（exit 5）／額度用完（6）：順手更新驗證結果；其他失敗不動它
    for fail, want in (("write:5", "auth"), ("write:6", "quota"), ("collect:3", "quota")):
        os.environ["FAKE_FAIL"] = fail
        daily.RECORD.unlink()
        tick(service)
        got = auth_health.read(service.health)
        assert got["ok"] is False and got["kind"] == want, fail
    os.environ["FAKE_FAIL"] = ""
    # 失敗通知：5、6 今天不會再重跑 → 各寄一封；collect:3 之後還會重跑 → 不寄
    subjects = [n["subject"] for n in service.notices]
    assert len(subjects) == 2 and "exit 5" in subjects[0] and "exit 6" in subjects[1]

    # 自動重跑：寄信失敗的話只重跑 render、send，不再呼叫 writer
    long_ago = "2000-01-01T00:00:00+00:00"
    today_record(trigger="schedule", finished_at=long_ago, exit_code=1, step="send")
    reset(steps)
    tick(service)
    rec_ = daily.read_record()
    assert rec_["trigger"] == "retry" and rec_["exit_code"] == 0
    assert not (steps / "collect").exists() and not (steps / "write").exists() and (steps / "send").exists()
    assert len(service.notices) == 2  # 重跑成功：不寄

    # 重跑也失敗：寄一封，說已經重跑過
    today_record(trigger="schedule", finished_at=long_ago, exit_code=1, step="send")
    os.environ["FAKE_FAIL"] = "send:1"
    tick(service)
    os.environ["FAKE_FAIL"] = ""
    assert daily.read_record()["trigger"] == "retry" and "已經自動重跑過一次" in service.notices[-1]["text"]
    assert len(service.notices) == 3
    # heartbeat：成功一次一個 True；寄失敗通知時（最終失敗）一個 False；之後還會重跑的不送
    assert service.beats == [True, False, False, True, False]


def test_backup(tmp_path):
    service = service_for(tmp_path, time(23, 59), time(7, 30))
    fb = service.state / "feedback.jsonl"
    fb.parent.mkdir(parents=True, exist_ok=True)
    sunday, monday = tpe("2026-11-08 08:00"), tpe("2026-11-09 08:00")
    service.backup(sunday)
    assert service.notices == [] and not (service.state / "backup_week").exists()  # 沒有回饋
    service.backed_up = None
    fb.write_text('{"uid": "a"}\n{"uid": "b"}\n')
    (service.state / "kg_flags.jsonl").write_text('{"uid": "a", "flag": "wrong"}\n')
    service.backup(tpe("2026-11-08 07:00"))  # 週日但還沒到 check_at
    service.backup(monday)
    assert service.notices == []
    service.backup(sunday)
    n = service.notices[-1]
    assert "2 列" in n["subject"] and n["attachments"][0]["filename"] == "feedback-2026-11-08.jsonl"
    assert Path(n["attachments"][0]["path"]).read_text() == fb.read_text()
    assert n["attachments"][1]["filename"] == "kg_flags-2026-11-08.jsonl" and "flag" in Path(n["attachments"][1]["path"]).read_text()
    assert (service.state / "backup_week").read_text() == "2026-W45"
    service.backup(tpe("2026-11-08 09:00"))
    again = service_for(tmp_path, time(23, 59), time(7, 30))  # 重啟：讀 backup_week，同週不重寄
    again.backup(tpe("2026-11-08 10:00"))
    assert len(service.notices) == 1 and again.notices == []
    fail = service_for(tmp_path, time(23, 59), time(7, 30))
    fail.notifier = lambda notice: False
    fail.backup(tpe("2026-11-15 08:00"))  # 寄不出去：不寫 backup_week
    assert (service.state / "backup_week").read_text() == "2026-W45"


SIGTERM_CHILD = """import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from helpers import fake_steps
from newsletter_worker import service
service.auth_health.run_check = lambda: {"ok": True, "kind": "ok", "message": ""}  # 不跑真的驗證
with fake_steps(Path(sys.argv[1])):
    os.environ.update(FAKE_FAIL="write:hang", NEWSLETTER_RUN_AT="00:00")
    sys.exit(service.main(["--host", "127.0.0.1", "--port", "0"]))
"""


@pytest.mark.skipif(os.name == "nt", reason="Windows 沒有 SIGTERM：送出去就是 TerminateProcess，程序沒機會收尾")
def test_sigterm(tmp_path):
    """真的程序收 SIGTERM：子程序被終止、紀錄寫好、期限內離開。"""
    proc = subprocess.Popen([sys.executable, "-c", SIGTERM_CHILD, str(tmp_path), str(Path(__file__).parent)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "NEWSLETTER_ROOT": str(tmp_path)})
    try:
        wait((tmp_path / "write").exists)  # 啟動就補跑（00:00 早就過了），卡在 write
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=15)[0]
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, out
    rec_ = json.loads((tmp_path / "state" / "last_run.json").read_text(encoding="utf-8"))
    assert rec_["exit_code"] == daily.EXIT_STOPPED and rec_["finished_at"] and rec_["trigger"] in ("catchup", "schedule")
    assert "收到停止" in out and f"exit {daily.EXIT_STOPPED}" in out
    assert not (tmp_path / "render").exists()


def test_schedule_off(steps):
    """測試區：不排程、不補跑、不驗認證、不寄提醒，status 沒有下次執行時間；POST /run 照常。"""
    service = service_for(steps, time(0, 0), time(0, 0))
    service.scheduled = False
    service.token_status = lambda: EXPIRING
    service.tick(datetime.now(timezone.utc))
    assert service.current is None and service.checks == [] and service.notices == []
    assert service.status()["next_run_at"] is None
    assert service.start("manual") is not None
    service._worker.join(15)
    assert daily.read_record()["exit_code"] == 0
