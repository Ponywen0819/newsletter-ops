"""使用者對新聞事實的判定：state/kg_flags.jsonl（只 append，同一個 uid 以最後一筆為準）。

wrong＝這則的事實有誤（它的事件失效，它對別則的矛盾／撤回也不算）；trusted＝這則可信（不被自動判定失效）；''＝取消。
網頁的「事實有誤」與 newsletter-kg --flag 寫這裡，writer 的 KG 每次產生背景時讀。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from newsletter_shared import ROOT
from newsletter_shared.feedback import append_rows
from newsletter_shared.lock import locked

PATH = ROOT / "state" / "kg_flags.jsonl"
FLAGS = {"wrong", "trusted", ""}


def read(path: Path = PATH) -> dict[str, str]:
    """uid → wrong／trusted（取消的不在裡面）。"""
    rows: dict[str, str] = {}
    if path.exists():
        with locked(path):
            lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip():
                row = json.loads(line)
                rows[row["uid"]] = row.get("flag", "")
    return {uid: flag for uid, flag in rows.items() if flag}


def append(uid: str, flag: str, path: Path = PATH) -> dict:
    if flag not in FLAGS:
        raise ValueError(f"flag 只能是 {sorted(FLAGS)}")
    row = {"uid": uid, "flag": flag, "at": datetime.now(timezone.utc).isoformat()}
    with locked(path):
        append_rows(path, [row])
    return row


if __name__ == "__main__":  # 自我檢查：uv run --project packages/shared python -m newsletter_shared.kg_flags
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "kg_flags.jsonl"
        assert read(p) == {}
        append("a" * 16, "wrong", p)
        append("b" * 16, "trusted", p)
        append("a" * 16, "", p)
        assert read(p) == {"b" * 16: "trusted"}
        try:
            append("c" * 16, "maybe", p)
            raise AssertionError("bad flag accepted")
        except ValueError:
            pass
    print("kg_flags selftest ok")
