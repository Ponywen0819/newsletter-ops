#!/usr/bin/env python3
"""跨程序的檔案鎖（flock）。

用法：uv run --project packages/shared python -m newsletter_shared.lock --selftest

    with locked(path):             # 等到拿到為止
    with locked(path, timeout=0):  # 搶不到就丟 LockBusy

鎖的是 `<path>.lock`，所以 path 可以被 os.replace 或還不存在。不可巢狀：同一執行緒重複鎖同一個 path 會丟 RuntimeError。
"""
from __future__ import annotations

import fcntl
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

POLL_SECONDS = 0.05  # flock 沒有內建逾時，有 timeout 時改成不阻塞地輪詢
_held = threading.local()  # 這個執行緒目前拿著的鎖，用來擋巢狀


class LockBusy(Exception):
    """timeout 內沒搶到鎖。"""


@contextmanager
def locked(path: Path, timeout: float | None = None):
    """獨占鎖。timeout=None 等到拿到；0 是試一次；其他是最多等幾秒。搶不到丟 LockBusy。"""
    lock = path.parent / (path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock.resolve())
    held = _held.__dict__.setdefault("keys", set())
    if key in held:
        raise RuntimeError(f"{path.name} 的鎖這個執行緒已經拿著了，不可巢狀使用")
    with lock.open("a") as fh:  # 關檔時自動解鎖
        if timeout is None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockBusy(f"{path.name} 被別的程序鎖著（等了 {timeout} 秒）") from None
                    time.sleep(POLL_SECONDS)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)


def selftest() -> None:
    import subprocess
    import tempfile

    child = ("import pathlib, sys\n"
             "from newsletter_shared.lock import locked\n"
             "with locked(pathlib.Path(sys.argv[1])):\n"
             "    print('held', flush=True)\n"
             "    sys.stdin.read()\n")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state" / "x.json"

        with locked(path):
            pass
        with locked(path, timeout=0):
            pass

        # 巢狀：立刻報錯，而且報錯後鎖仍然正常放掉
        try:
            with locked(path), locked(path):
                raise AssertionError("巢狀應該丟 RuntimeError")
        except RuntimeError:
            pass
        with locked(path, timeout=0):
            pass

        # 不同執行緒拿同一個 path 是排隊（LockBusy），不是巢狀（RuntimeError）
        holding, release, seen = threading.Event(), threading.Event(), []

        def holder() -> None:
            with locked(path):
                holding.set()
                release.wait(5)

        def contender() -> None:
            try:
                with locked(path, timeout=0):
                    seen.append("got")
            except LockBusy:
                seen.append("busy")

        t = threading.Thread(target=holder)
        t.start()
        assert holding.wait(5)
        c = threading.Thread(target=contender)
        c.start()
        c.join()
        release.set()
        t.join()
        assert seen == ["busy"], seen

        # 別的程序拿著：timeout=0 馬上放棄，timeout=0.2 至少等滿 0.2 秒
        proc = subprocess.Popen([sys.executable, "-c", child, str(path)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        try:
            assert proc.stdout.readline() == "held\n"
            for wait in (0, 0.2):
                start = time.monotonic()
                try:
                    with locked(path, timeout=wait):
                        raise AssertionError("別的程序拿著，不該拿得到")
                except LockBusy:
                    assert time.monotonic() - start >= wait
        finally:
            proc.kill()
            proc.wait()
        with locked(path, timeout=0):
            pass
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        print("用法：python -m newsletter_shared.lock --selftest", file=sys.stderr)
        raise SystemExit(2)
