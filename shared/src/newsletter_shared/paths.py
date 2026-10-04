"""repo 根目錄：全 repo 唯一推算路徑的地方，其他模組一律 `from newsletter_shared.paths import ROOT`。

用法：python -m newsletter_shared.paths   # 自我檢查

成員以 editable 安裝（uv workspace 的預設），`__file__` 因此仍在原始碼樹裡：
shared/src/newsletter_shared/paths.py 往上三層是 repo 根。
reports/、data/、state/、logs/、config/ 都在 ROOT 底下，不在任何成員目錄裡。
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
