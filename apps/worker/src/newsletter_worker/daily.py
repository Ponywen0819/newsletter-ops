#!/usr/bin/env python3
"""每日流程：蒐集 → 寫報告 → render → 寄信，整條握一把防重疊的鎖。

入口在 service.py：worker --once 跑一次、worker 常駐排程，都進 run_pipeline()。

任一步失敗就停，exit code 是該步的；被訊號殺掉是 128+訊號；鎖被占用是 75（EX_TEMPFAIL）；
超過 TIMEOUTS 的時限是 124（和 timeout(1) 一樣），免得卡住的步驟一直握著鎖、之後每天都跑不了。
拿到鎖才算執行：開始與結束都寫進 state/last_run.json（排程服務用它判斷今天跑過沒、web 用它顯示失敗）；
失敗時 step 是哪一步（collect / write / render / send）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import IO

from newsletter_shared import ROOT, atomic, lock, metrics, today, tz

TZ = tz()  # log 的時間跟 config 的 timezone，不跟容器的 TZ
EXIT_BUSY = 75
EXIT_STOPPED = 128 + signal.SIGTERM
EXIT_TIMEOUT = 124
KILL_AFTER = 30  # 秒；送 SIGTERM 後還不結束就 SIGKILL
TIMEOUTS = {"collect": 30 * 60, "write": 60 * 60, "render": 5 * 60, "send": 5 * 60}  # 秒，平常遠低於此
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
    print(f"=== {datetime.now(TZ):%Y-%m-%d %H:%M:%S} === {message}", file=log, flush=True)


def step(log: IO[str], key: str, *args: str, input: str | None = None, capture: bool = False,
         stop: threading.Event | None = None) -> tuple[int, str]:
    """跑 MODULES[key] 一步，回傳 (exit code, stdout)。逾時是 TIMEOUTS[key]，其餘見 spawn。"""
    return spawn(log, MODULES[key], *args, limit=TIMEOUTS.get(key), input=input, capture=capture, stop=stop)


def spawn(log: IO[str], module: str, *args: str, limit: float | None = None, input: str | None = None,  # noqa: PLR0913 都是 keyword-only
          capture: bool = False, stop: threading.Event | None = None,
          env: dict[str, str] | None = None) -> tuple[int, str]:
    """`python -m module` 一次，回傳 (exit code, stdout)。stdout 只有 capture 時才收；否則和 stderr 一起進 log。
    stop 被設起來、或跑超過 limit 秒，就對子程序送 SIGTERM；KILL_AFTER 秒後還在就 SIGKILL。超時回 EXIT_TIMEOUT。
    env 沒給就繼承目前的環境變數。"""
    proc = subprocess.Popen([sys.executable, "-m", module, *args], cwd=ROOT, encoding="utf-8", env=env,
                            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else log, stderr=log)
    out, sent, t0 = "", False, time.monotonic()
    timed_out, termed_at = False, None
    while True:
        try:
            out = proc.communicate(None if sent else input, timeout=0.5)[0] or ""
            break
        except subprocess.TimeoutExpired:
            sent = True  # communicate 重試時不能再給 input，已送出的不會遺失
            now = time.monotonic()
            if termed_at is None and limit is not None and now - t0 > limit:
                timed_out = True
                say(log, f"超過 {limit:.0f} 秒，停止：{module}")
            if termed_at is None and (timed_out or (stop is not None and stop.is_set())):
                proc.terminate()
                termed_at = now
            elif termed_at is not None and now - termed_at > KILL_AFTER:
                proc.kill()
    if timed_out:
        return EXIT_TIMEOUT, out
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
            code, failed, t0 = 1, [], time.monotonic()  # _run 丟例外時記成失敗
            os.environ[metrics.RUN_ID_ENV] = f"{datetime.now(TZ):%Y%m%dT%H%M%S}-{trigger}"  # 子程序繼承；有鎖，同時只有一條
            try:
                code = _run(collect_args, log, stop, failed, start)
                return code
            finally:
                step_ = failed[-1] if failed else None
                atomic.write_text(RECORD, json.dumps({**rec, "finished_at": now_iso(), "exit_code": code, "step": step_}))
                metrics.record("pipeline", seconds=round(time.monotonic() - t0, 1), trigger=trigger, start=start,
                               exit_code=code, step=step_)
                os.environ.pop(metrics.RUN_ID_ENV, None)
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
        code, out = step(log, key, *args, input=input, capture=capture, stop=stop)
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
