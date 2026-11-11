#!/usr/bin/env python3
"""跨程序的檔案鎖（flock）。

    with locked(path):             # 等到拿到為止
    with locked(path, timeout=0):  # 搶不到就丟 LockBusy

鎖的是 `<path>.lock`，所以 path 可以被 os.replace 或還不存在。不可巢狀：同一執行緒重複鎖同一個 path 會丟 RuntimeError。
"""
from __future__ import annotations

import fcntl
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
