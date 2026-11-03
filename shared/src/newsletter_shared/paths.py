"""repo 根目錄，全 repo 唯一推算路徑的地方。

用法：python -m newsletter_shared.paths   # 自我檢查
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def selftest() -> None:
    assert (ROOT / "uv.lock").is_file(), f"ROOT 不是 repo 根（是不是非 editable 安裝？）：{ROOT}"
    assert (ROOT / "config").is_dir(), f"ROOT 底下沒有 config/：{ROOT}"
    print("ok")


if __name__ == "__main__":
    selftest()
