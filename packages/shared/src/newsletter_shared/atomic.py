"""整檔取代：先寫暫存檔、fsync，再 os.replace。寫到一半被中斷（kill、斷電、磁碟滿），舊檔仍完整、不留暫存檔。"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    """權限 600（mkstemp 的預設）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:  # 原樣寫入：Windows 不把 \n 換成 \r\n
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
