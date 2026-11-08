#!/usr/bin/env python3
"""每日流程：蒐集 → 寫報告 → render → 寄信，整條握一把防重疊的鎖。

入口在 service.py：worker --once 跑一次、worker 常駐排程，都進 run_pipeline()。

任一步失敗就停，exit code 是該步的；被訊號殺掉是 128+訊號；鎖被占用是 75（EX_TEMPFAIL）。
拿到鎖才算執行：開始與結束都寫進 state/last_run.json（排程服務用它判斷今天跑過沒、web 用它顯示失敗）；
失敗時 step 是哪一步（collect / write / render / send）。
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator

from newsletter_shared import ROOT, atomic, lock, today

EXIT_BUSY = 75
EXIT_STOPPED = 128 + signal.SIGTERM
LOCK = ROOT / "state" / "pipeline"  # 實際鎖的是 state/pipeline.lock
RECORD = ROOT / "state" / "last_run.json"
STEPS = ("collect", "write", "render", "send")
MODULES = {
    "collect": "newsletter_collect.run",
    "write": "newsletter_writer.write",
    "render": "newsletter_notify.render_email",
    "send": "newsletter_notify.send_email",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_record() -> dict | None:
    try:
        rec = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def say(log: IO[str], message: str) -> None:
    print(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} === {message}", file=log, flush=True)


def step(log: IO[str], module: str, *args: str, input: str | None = None, capture: bool = False,
         stop: threading.Event | None = None) -> tuple[int, str]:
    """跑一步，回傳 (exit code, stdout)。stdout 只有 capture 時才收；否則和 stderr 一起進 log。
    stop 被設起來就對子程序送 SIGTERM、等它結束。"""
    proc = subprocess.Popen([sys.executable, "-m", module, *args], cwd=ROOT, encoding="utf-8",
                            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else log, stderr=log)
    out, sent = "", False
    while True:
        try:
            out = proc.communicate(None if sent else input, timeout=0.5)[0] or ""
            break
        except subprocess.TimeoutExpired:
            sent = True  # communicate 重試時不能再給 input，已送出的不會遺失
            if stop is not None and stop.is_set() and proc.poll() is None:
                proc.terminate()
    code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    return code, out


def resume_from(failed: str | None) -> str:
    """重跑時從哪一步開始：失敗的那步（前面的產出今天已經有了）；send 要 render 的 stdout，所以從 render。"""
    if failed == "send":
        return "render"
    return failed if failed in STEPS else "collect"


def run_pipeline(collect_args: list[str], log: IO[str], trigger: str = "cli", stop: threading.Event | None = None,
                 start: str = "collect") -> int:
    """trigger：schedule / catchup / retry / manual / cli，寫進紀錄；排程服務靠 retry 判斷重跑過沒。"""
    try:
        with lock.locked(LOCK, timeout=0):
            rec = {"date": today(), "trigger": trigger, "started_at": now_iso(),
                   "finished_at": None, "exit_code": None, "step": None}
            atomic.write_text(RECORD, json.dumps(rec))
            code, failed = 1, []  # _run 丟例外時記成失敗
            try:
                code = _run(collect_args, log, stop, failed, start)
                return code
            finally:
                atomic.write_text(RECORD, json.dumps({**rec, "finished_at": now_iso(), "exit_code": code,
                                                      "step": failed[-1] if failed else None}))
    except lock.LockBusy:
        say(log, "已有另一條 pipeline 在跑（state/pipeline.lock 被占用），這次不執行")
        return EXIT_BUSY


def _run(collect_args: list[str], log: IO[str], stop: threading.Event | None = None,
         failed: list[str] | None = None, start: str = "collect") -> int:
    """failed：失敗（或被停止）的那一步會 append 進去。start 之前的步驟不跑（重跑時沿用今天的產出）。"""
    failed = [] if failed is None else failed
    skip = set(STEPS[:STEPS.index(start)])

    def go(label: str, key: str, *args: str, input: str | None = None, capture: bool = False) -> tuple[int, str]:
        if key in skip:
            say(log, f"沿用今天的產出，不重跑：{label}")
            return 0, ""
        if stop is not None and stop.is_set():
            say(log, f"收到停止，不執行：{label}")
            failed.append(key)
            return EXIT_STOPPED, ""
        say(log, label)
        code, out = step(log, MODULES[key], *args, input=input, capture=capture, stop=stop)
        if code:
            say(log, f"失敗，exit {code}：{label}")
            failed.append(key)
        return code, out

    code, _ = go(" ".join(["newsletter-collect", *collect_args]), "collect", *collect_args)
    if code:
        return code
    code, _ = go("newsletter-write", "write")
    if code:
        return code
    code, meta = go("newsletter-render", "render", capture=True)  # stdout 是一行 JSON，下一步的輸入
    if code:
        return code
    code, _ = go("newsletter-send", "send", input=meta)
    if code:
        return code
    say(log, "完成")
    return 0


# 自我檢查用的假步驟：記下收到的參數與 stdin，依 FAKE_FAIL=<步驟>:<exit code、kill 或 hang> 失敗
FAKE_STEP = '''import os, sys, time
name = "NAME"
out = os.environ["FAKE_OUT"]
data = sys.stdin.read() if name == "send" else ""
open(f"{out}/{name}", "w").write(" ".join(sys.argv[1:]) + "|" + data)
print(name, "stderr", file=sys.stderr)
if name == "render":
    print('{"subject": "s"}')
fail = os.environ.get("FAKE_FAIL", "").split(":")
if fail[0] == name:
    if fail[1] == "kill":
        os.kill(os.getpid(), 9)
    if fail[1] == "hang":
        time.sleep(60)
    sys.exit(int(fail[1]))
'''


@contextlib.contextmanager
def fake_steps(tmp: Path) -> Iterator[None]:
    """四步換成假步驟，鎖與紀錄放進 tmp；service 的自我檢查也用。"""
    global MODULES, LOCK, RECORD  # noqa: PLW0603
    saved, env = (MODULES, LOCK, RECORD), dict(os.environ)
    for name in saved[0]:
        (tmp / f"fake_{name}.py").write_text(FAKE_STEP.replace("NAME", name), encoding="utf-8")
    os.environ.update(FAKE_OUT=str(tmp), PYTHONPATH=str(tmp), FAKE_FAIL="")
    MODULES = {name: f"fake_{name}" for name in saved[0]}
    LOCK, RECORD = tmp / "state" / "pipeline", tmp / "state" / "last_run.json"
    try:
        yield
    finally:
        MODULES, LOCK, RECORD = saved
        os.environ.clear()
        os.environ.update(env)


def selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d, fake_steps(Path(d)):
        tmp = Path(d)

        def run(*args: str, fail: str = "", **kw) -> tuple[int, str]:
            os.environ["FAKE_FAIL"] = fail
            for name in MODULES:
                (tmp / name).unlink(missing_ok=True)
            with tempfile.TemporaryFile("w+", encoding="utf-8") as log:
                code = run_pipeline(list(args), log, **kw)
                log.seek(0)
                return code, log.read()

        def ran(name: str) -> bool:
            return (tmp / name).exists()

        # 全部成功：參數轉給 collect、render 的 stdout 原封不動餵給 send、各步驟的 stderr 進 log
        code, text = run("--lookback", "72", trigger="schedule")
        assert code == 0, (code, text)
        assert (tmp / "collect").read_text() == "--lookback 72|"
        assert (tmp / "send").read_text() == '|{"subject": "s"}\n'
        assert "write stderr" in text and "render stderr" in text and text.rstrip().endswith("完成"), text
        assert '"subject"' not in text
        rec = read_record()
        assert rec is not None and rec["trigger"] == "schedule" and rec["exit_code"] == 0 and rec["finished_at"], rec
        assert rec["date"] == today() and rec["started_at"] <= rec["finished_at"], rec
        assert rec["step"] is None, rec

        # 任一步失敗：後面的不跑、exit code 是該步的、log 一行說明、紀錄記下 exit code；失敗也會放鎖
        code, text = run(fail="write:5")
        assert code == 5 and ran("collect") and ran("write") and not ran("render") and not ran("send")
        assert "失敗，exit 5：newsletter-write" in text and "完成" not in text
        assert read_record()["exit_code"] == 5 and read_record()["trigger"] == "cli" and read_record()["step"] == "write"
        code, text = run(fail="collect:3")
        assert code == 3 and ran("collect") and not ran("write")
        code, text = run(fail="render:kill")
        assert code == 128 + 9 and ran("render") and not ran("send") and read_record()["exit_code"] == 128 + 9
        assert read_record()["step"] == "render"

        # stop：對執行中的子程序送 SIGTERM、後面的不跑；紀錄有 finished_at 與 143
        stop, t0 = threading.Event(), time.monotonic()
        threading.Timer(1, stop.set).start()
        code, text = run(fail="write:hang", stop=stop)
        assert code == EXIT_STOPPED and time.monotonic() - t0 < 10 and not ran("render"), (code, text)
        rec = read_record()
        assert rec["exit_code"] == EXIT_STOPPED and rec["finished_at"], rec
        code, _ = run(stop=stop)  # 已經要停：一步都不跑
        assert code == EXIT_STOPPED and not ran("collect")

        # 從中間開始：前面的不跑；send 失敗要從 render 重來（send 吃 render 的 stdout）
        code, text = run(start=resume_from("send"))
        assert code == 0 and not ran("collect") and not ran("write") and ran("render"), text
        assert (tmp / "send").read_text() == '|{"subject": "s"}\n' and "沿用今天的產出，不重跑：newsletter-write" in text
        code, _ = run(start=resume_from("write"))
        assert code == 0 and not ran("collect") and ran("write")
        assert resume_from(None) == resume_from("bogus") == "collect"

        # 別的執行緒（等同別的程序）拿著鎖：這次不執行，一步都不跑、不寫紀錄；放掉之後恢復正常
        holding, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.locked(LOCK):
                holding.set()
                release.wait(5)

        before = RECORD.read_text()
        t = threading.Thread(target=hold)
        t.start()
        assert holding.wait(5)
        try:
            code, text = run(trigger="manual")
        finally:
            release.set()
            t.join()
        assert code == EXIT_BUSY and "已有另一條 pipeline" in text and not any(ran(n) for n in MODULES)
        assert RECORD.read_text() == before
        assert run()[0] == 0
    print("ok")
