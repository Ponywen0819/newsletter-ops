"""手動執行（POST /run、POST /job）：記進 state/last_job.json，GET /status 帶出來給 web 的維運頁。"""
import http.client
import json
import threading
from datetime import datetime

import pytest
from helpers import AT, ran, service_for, wait

from newsletter_shared import lock
from newsletter_worker import daily
from newsletter_worker import service as svc
from newsletter_worker.service import make_server

FAKE_JOB = '''import os, sys
print("job", " ".join(sys.argv[1:]), os.environ.get("NEWSLETTER_RUN_LABEL"), file=sys.stderr)
sys.exit(int(os.environ.get("FAKE_JOB_EXIT", "0")))
'''


@pytest.fixture
def jobs(steps, monkeypatch):
    (steps / "fake_job.py").write_text(FAKE_JOB, encoding="utf-8")
    monkeypatch.setattr(svc, "JOBS", {kind: ("fake_job", args, 30) for kind, (_, args, _) in svc.JOBS.items()})
    return steps


@pytest.fixture
def api(jobs):
    """(service, req)：真的 HTTP 伺服器。"""
    service = service_for(jobs, AT)
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

    yield service, req
    server.shutdown()
    server.server_close()


def finished(req) -> dict | None:
    job = req("GET", "/status")[1]["job"]
    return job if job and job.get("finished_at") else None


def test_status_fields(api):
    service, req = api
    st = req("GET", "/status")[1]
    assert st["scheduled"] is True and st["run_at"] == "08:00" and st["job"] is None
    service.scheduled = False
    assert req("GET", "/status")[1]["scheduled"] is False


def test_run_records_job(api, jobs):
    _, req = api
    status, body = req("POST", "/run?from=render")
    assert status == 202 and body["id"] and body["started_at"]
    assert req("GET", "/status")[1]["current"]["kind"] == "run"
    wait(lambda: finished(req) is not None)
    job = finished(req)
    assert job["id"] == body["id"] and job["kind"] == "run" and job["from"] == "render" and job["exit_code"] == 0
    assert job["log"] == f"logs/{datetime.now():%Y-%m}.log" and isinstance(job["offset"], int)
    assert not ran(jobs, "collect") and ran(jobs, "render") and ran(jobs, "send")
    log = (jobs / job["log"]).read_text(encoding="utf-8")[job["offset"]:]
    assert "完成" in log
    assert json.loads((jobs / "state" / "last_job.json").read_text(encoding="utf-8"))["id"] == body["id"]


@pytest.mark.parametrize("kind, args", [("kg-rebuild", "--rebuild"), ("sources-check", "--dry-run --no-search")])
def test_jobs(api, jobs, kind, args):
    _, req = api
    status, body = req("POST", f"/job?kind={kind}")
    assert status == 202
    wait(lambda: finished(req) is not None)
    job = finished(req)
    assert job["id"] == body["id"] and job["kind"] == kind and job["exit_code"] == 0 and job["log"] == f"logs/ops-{kind}.log"
    log = (jobs / job["log"]).read_text(encoding="utf-8")
    assert f"job {args} ops" in log and "完成" in log  # metrics 標籤是 ops，不混進每日統計
    assert daily.read_record() is None  # 不是每日流程，不動 last_run.json


def test_job_failure(api, jobs, monkeypatch):
    _, req = api
    monkeypatch.setenv("FAKE_JOB_EXIT", "3")
    req("POST", "/job?kind=sources-check")
    wait(lambda: finished(req) is not None)
    assert finished(req)["exit_code"] == 3
    assert "失敗，exit 3" in (jobs / "logs" / "ops-sources-check.log").read_text(encoding="utf-8")


def hold_lock():
    """另一條執行緒握著 pipeline 鎖（同一條執行緒不能巢狀拿同一把鎖）。回傳放開的函式。"""
    got, release = threading.Event(), threading.Event()

    def run():
        with lock.locked(daily.LOCK):
            got.set()
            release.wait(15)

    t = threading.Thread(target=run)
    t.start()
    got.wait(5)
    return lambda: (release.set(), t.join())


def test_busy_and_invalid(api):
    service, req = api
    free = hold_lock()  # 別的程序在跑每日流程
    try:
        assert req("POST", "/job?kind=kg-rebuild")[0] == 409
        assert req("POST", "/run")[0] == 409
    finally:
        free()
    assert service.current is None and req("GET", "/status")[1]["job"] is None  # 沒執行就不記
    assert req("POST", "/job?kind=rm")[0] == 400
    assert req("POST", "/job")[0] == 400
    assert req("POST", "/run?from=deploy")[0] == 400
    assert req("POST", "/jobs")[0] == 404


def test_schedule_off_still_accepts(api, jobs):
    """測試區不排程，但手動執行照常。"""
    service, req = api
    service.scheduled = False
    assert req("POST", "/run")[0] == 202
    wait(lambda: finished(req) is not None)
    assert finished(req)["exit_code"] == 0 and ran(jobs, "collect")
