"""假的四步與共用的小工具；SIGTERM 測試的子程序也 import 這裡。"""
from __future__ import annotations

import contextlib
import os
import time as clock
from datetime import datetime, time
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from newsletter_worker import daily
from newsletter_worker.service import Service

TPE = ZoneInfo("Asia/Taipei")
AT = time(8, 0)
CHECK_AT = time(7, 30)

# 記下收到的參數與 stdin，依 FAKE_FAIL=<步驟>:<exit code、kill、hang 或 stubborn> 失敗
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
    if fail[1] == "stubborn":  # 不理 SIGTERM
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    sys.exit(int(fail[1]))
'''


@contextlib.contextmanager
def fake_steps(tmp: Path) -> Iterator[None]:
    """四步換成假步驟，鎖與紀錄放進 tmp。"""
    saved, env = (daily.MODULES, daily.LOCK, daily.RECORD, daily.TIMEOUTS, daily.KILL_AFTER), dict(os.environ)
    for name in daily.MODULES:
        (tmp / f"fake_{name}.py").write_text(FAKE_STEP.replace("NAME", name), encoding="utf-8")
    os.environ.update(FAKE_OUT=str(tmp), PYTHONPATH=str(tmp), FAKE_FAIL="")
    daily.MODULES = {name: f"fake_{name}" for name in saved[0]}
    daily.LOCK, daily.RECORD = tmp / "state" / "pipeline", tmp / "state" / "last_run.json"
    daily.TIMEOUTS = dict(saved[3])
    try:
        yield
    finally:
        daily.MODULES, daily.LOCK, daily.RECORD, daily.TIMEOUTS, daily.KILL_AFTER = saved
        os.environ.clear()
        os.environ.update(env)


def ran(tmp: Path, name: str) -> bool:
    return (tmp / name).exists()


def reset(tmp: Path) -> None:
    for name in daily.MODULES:
        (tmp / name).unlink(missing_ok=True)


def tpe(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


def service_for(tmp: Path, at: time, check_at: time = CHECK_AT) -> Service:
    """假的認證驗證、通知、heartbeat：記下呼叫，不跑子程序、不寄信。"""
    service = Service(at, TPE, check_at, tmp / "logs", tmp / "state" / "auth_health.json")
    service.checks = []  # type: ignore[attr-defined]
    service.checker = lambda: service.checks.append(1) or {"ok": True, "kind": "ok", "message": ""}  # type: ignore[attr-defined]
    service.notices = []  # type: ignore[attr-defined]
    service.notifier = service.notices.append  # type: ignore[attr-defined]
    service.beats = []  # type: ignore[attr-defined]
    service.beat = service.beats.append  # type: ignore[attr-defined]
    service.token_status = lambda: {"state": "ok", "days_left": 200}
    return service


def wait(cond, seconds: float = 15) -> None:
    deadline = clock.monotonic() + seconds
    while not cond():
        assert clock.monotonic() < deadline, "等太久"
        clock.sleep(0.05)
