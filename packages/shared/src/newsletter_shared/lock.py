#!/usr/bin/env python3
"""跨程序的檔案鎖（POSIX 用 flock，Windows 用 msvcrt.locking）。

    with locked(path):             # 等到拿到為止
    with locked(path, timeout=0):  # 搶不到就丟 LockBusy

鎖的是 `<path>.lock`，所以 path 可以被 os.replace 或還不存在。不可巢狀：同一執行緒重複鎖同一個 path 會丟 RuntimeError。
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO

POLL_SECONDS = 0.05  # 有 timeout（或 Windows 上要一直等）時改成不阻塞地輪詢
_held = threading.local()  # 這個執行緒目前拿著的鎖，用來擋巢狀

if os.name == "nt":
    import msvcrt

    def _lock(fh: IO, blocking: bool) -> bool:
        # msvcrt.locking 鎖的是「目前位置起的 N bytes」，固定鎖第 0 個 byte（超過檔尾也可以鎖）。
        # LK_LOCK 只重試 10 秒就放棄，所以一律用 LK_NBLCK，要等的話由呼叫端輪詢。
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh: IO) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    _CAN_BLOCK = False
else:
    import fcntl

    def _lock(fh: IO, blocking: bool) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fh: IO) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)

    _CAN_BLOCK = True


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
    with lock.open("a") as fh:  # 關檔時作業系統也會解鎖，這裡仍明確解鎖
        if timeout is None and _CAN_BLOCK:
            _lock(fh, blocking=True)
        else:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not _lock(fh, blocking=False):
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockBusy(f"{path.name} 被別的程序鎖著（等了 {timeout} 秒）")
                time.sleep(POLL_SECONDS)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            _unlock(fh)
