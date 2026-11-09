"""資料根目錄 ROOT（config/、reports/、data/、state/、logs/ 所在）：NEWSLETTER_ROOT，沒設就是啟動時的工作目錄。
報告日期的時區 tz()：config/config.json 的 timezone，各模組都從這裡拿，日期才不會對不上。"""
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("NEWSLETTER_ROOT") or Path.cwd()).resolve()
DEFAULT_TZ = "Asia/Taipei"


def tz(root: Path = ROOT) -> ZoneInfo:
    try:
        name = json.loads((root / "config" / "config.json").read_text(encoding="utf-8")).get("timezone")
    except (OSError, ValueError, AttributeError):
        name = None
    return ZoneInfo(name or DEFAULT_TZ)


def today(root: Path = ROOT) -> str:
    """報告日期（YYYY-MM-DD），也是 reports/、data/curated/ 的檔名。"""
    return f"{datetime.now(tz(root)):%Y-%m-%d}"
