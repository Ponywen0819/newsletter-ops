import subprocess
import sys
import threading
import time

import pytest

from newsletter_shared.lock import LockBusy, locked

CHILD = ("import pathlib, sys\n"
         "from newsletter_shared.lock import locked\n"
         "with locked(pathlib.Path(sys.argv[1])):\n"
         "    print('held', flush=True)\n"
         "    sys.stdin.read()\n")


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / "x.json"


def test_basic(path):
    with locked(path):
        pass
    with locked(path, timeout=0):
        pass


def test_nested_raises_and_releases(path):
    with pytest.raises(RuntimeError), locked(path), locked(path):
        pass
    with locked(path, timeout=0):
        pass


def test_other_thread_queues(path):
    """不同執行緒拿同一個 path 是排隊（LockBusy），不是巢狀（RuntimeError）。"""
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
    assert seen == ["busy"]


def test_other_process(path):
    """別的程序拿著：timeout=0 馬上放棄，timeout=0.2 至少等滿 0.2 秒；程序被殺後鎖會放掉。"""
    proc = subprocess.Popen([sys.executable, "-c", CHILD, str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline() == "held\n"
        for wait in (0, 0.2):
            start = time.monotonic()
            with pytest.raises(LockBusy), locked(path, timeout=wait):
                pass
            assert time.monotonic() - start >= wait
    finally:
        proc.kill()
        proc.wait()
    with locked(path, timeout=0):
        pass
