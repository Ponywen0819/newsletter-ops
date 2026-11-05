"""資料根目錄 ROOT（config/、reports/、data/、state/、logs/ 所在）：NEWSLETTER_ROOT，沒設就是啟動時的工作目錄。"""
import os
from pathlib import Path

ROOT = Path(os.environ.get("NEWSLETTER_ROOT") or Path.cwd()).resolve()
