import os
import tempfile
import threading
import time

import pytest
from helpers import ran, reset

from newsletter_shared import lock, today
from newsletter_worker import daily
from newsletter_worker.daily import EXIT_BUSY, EXIT_STOPPED, EXIT_TIMEOUT, read_record, resume_from, run_pipeline


@pytest.fixture
def run(steps):
    def run_(*args: str, fail: str = "", **kw) -> tuple[int, str]:
        os.environ["FAKE_FAIL"] = fail
        reset(steps)
        with tempfile.TemporaryFile("w+", encoding="utf-8") as log:
            code = run_pipeline(list(args), log, **kw)
            log.seek(0)
            return code, log.read()
    return run_


def test_success(run, steps):
    """參數轉給 collect、render 的 stdout 原封不動餵給 send、各步驟的 stderr 進 log。"""
    code, text = run("--lookback", "72", trigger="schedule")
    assert code == 0, text
    assert (steps / "collect").read_text() == "--lookback 72|"
    assert (steps / "send").read_text() == '|{"subject": "s"}\n'
    assert "write stderr" in text and "render stderr" in text and text.rstrip().endswith("完成")
    assert '"subject"' not in text
    rec = read_record()
    assert rec is not None and rec["trigger"] == "schedule" and rec["exit_code"] == 0 and rec["finished_at"]
    assert rec["date"] == today() and rec["started_at"] <= rec["finished_at"]
    assert rec["step"] is None


def test_step_failure(run, steps):
    """後面的不跑、exit code 是該步的、log 一行說明、紀錄記下 exit code；失敗也會放鎖。"""
    code, text = run(fail="write:5")
    assert code == 5 and ran(steps, "collect") and ran(steps, "write") and not ran(steps, "render") and not ran(steps, "send")
    assert "失敗，exit 5：newsletter-write" in text and "完成" not in text
    assert read_record()["exit_code"] == 5 and read_record()["trigger"] == "cli" and read_record()["step"] == "write"
    code, _ = run(fail="collect:3")
    assert code == 3 and ran(steps, "collect") and not ran(steps, "write")
    code, _ = run(fail="render:kill")
    killed = 9 if os.name == "nt" else 128 + 9  # Windows 的 os.kill 是 TerminateProcess，exit code 就是那個數字
    assert code == killed and ran(steps, "render") and not ran(steps, "send") and read_record()["exit_code"] == killed
    assert read_record()["step"] == "render"


def test_stop(run, steps):
    """對執行中的子程序送 SIGTERM、後面的不跑；紀錄有 finished_at 與 143。"""
    stop, t0 = threading.Event(), time.monotonic()
    threading.Timer(1, stop.set).start()
    code, text = run(fail="write:hang", stop=stop)
    assert code == EXIT_STOPPED and time.monotonic() - t0 < 10 and not ran(steps, "render"), text
    rec = read_record()
    assert rec["exit_code"] == EXIT_STOPPED and rec["finished_at"]
    code, _ = run(stop=stop)  # 已經要停：一步都不跑
    assert code == EXIT_STOPPED and not ran(steps, "collect")


def test_timeout(run, monkeypatch):
    """SIGTERM 停下、exit 124、紀錄的 step 是那一步；不理 SIGTERM 的 KILL_AFTER 後 SIGKILL。"""
    daily.TIMEOUTS["write"], t0 = 1, time.monotonic()
    code, text = run(fail="write:hang")
    assert code == EXIT_TIMEOUT and time.monotonic() - t0 < 10, text
    assert "超過 1 秒" in text and read_record()["step"] == "write" and read_record()["exit_code"] == EXIT_TIMEOUT
    monkeypatch.setattr(daily, "KILL_AFTER", 1)
    t0 = time.monotonic()
    code, _ = run(fail="write:stubborn")
    assert code == EXIT_TIMEOUT and time.monotonic() - t0 < 10


def test_resume(run, steps):
    """前面的不跑；send 失敗要從 render 重來（send 吃 render 的 stdout）。"""
    code, text = run(start=resume_from("send"))
    assert code == 0 and not ran(steps, "collect") and not ran(steps, "write") and ran(steps, "render"), text
    assert (steps / "send").read_text() == '|{"subject": "s"}\n' and "沿用今天的產出，不重跑：newsletter-write" in text
    code, _ = run(start=resume_from("write"))
    assert code == 0 and not ran(steps, "collect") and ran(steps, "write")
    assert resume_from(None) == resume_from("bogus") == "collect"


def test_busy(run, steps):
    """別的執行緒（等同別的程序）拿著鎖：這次不執行，一步都不跑、不寫紀錄；放掉之後恢復正常。"""
    assert run()[0] == 0
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with lock.locked(daily.LOCK):
            holding.set()
            release.wait(5)

    before = daily.RECORD.read_text()
    t = threading.Thread(target=hold)
    t.start()
    assert holding.wait(5)
    try:
        code, text = run(trigger="manual")
    finally:
        release.set()
        t.join()
    assert code == EXIT_BUSY and "已有另一條 pipeline" in text and not any(ran(steps, n) for n in daily.MODULES)
    assert daily.RECORD.read_text() == before
    assert run()[0] == 0
