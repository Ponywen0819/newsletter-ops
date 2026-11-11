#!/usr/bin/env python3
"""OAuth token 的儲存與讀取，newsletter-web 與 newsletter-llm 共用。

token 唯一的來源是 state/oauth_token.json（/auth 貼上後存的，權限 600），不讀 CLAUDE_CODE_OAUTH_TOKEN：
換新要立刻生效，所有程序看到同一份。不支援 API key；scrub_environ() 移除優先序高於 OAuth 的來源。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, MutableMapping

from newsletter_shared import atomic, lock
from newsletter_shared import ROOT

TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
TOKEN_FILE_ENV = "NEWSLETTER_TOKEN_FILE"

# 依 Claude Code 文件的認證優先順序，這些來源都排在 CLAUDE_CODE_OAUTH_TOKEN 前面。
# apiKeyHelper 是 settings 的欄位、不是環境變數；本專案只載入專案層級的設定，沒有定義它。
SHADOWING_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

VALID_DAYS = 365   # setup-token 的效期；用儲存時間自己推算，伺服器端才是準的
WARN_DAYS = 30     # 剩幾天起算「即將到期」
MIN_LEN, MAX_LEN = 20, 512
TOKEN_RE = re.compile(r"[\x21-\x7e]+")  # 可見的 ASCII，不含空白與換行


@dataclass(frozen=True)
class Auth:
    source: str  # "stored-token"（--auth-check --token-from-env 驗證候選 token 時是 "env-token"）
    token: str = field(repr=False)


def token_path(root: Path = ROOT, environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    override = environ.get(TOKEN_FILE_ENV, "").strip()
    return Path(override) if override else root / "state" / "oauth_token.json"


def clean(raw: str) -> str:
    """貼上時常帶著前後的空白與換行。"""
    return raw.strip()


def check_format(token: str) -> str | None:
    """基本檢查，回傳錯誤訊息；None ＝ 通過。只擋明顯不是 token 的內容，真偽要實際呼叫才知道。"""
    if not token:
        return "token 是空的"
    if len(token) < MIN_LEN or len(token) > MAX_LEN:
        return f"token 長度需在 {MIN_LEN}~{MAX_LEN} 個字元"
    if not TOKEN_RE.fullmatch(token):
        return "token 只能是可見的 ASCII 字元，不能含空白或換行"
    return None


def save(token: str, path: Path, now: datetime | None = None) -> None:
    """權限 600，整檔取代：讀的人不會看到半寫入的內容。"""
    now = now or datetime.now(timezone.utc)
    with lock.locked(path):
        atomic.write_text(path, json.dumps({"token": token, "saved_at": now.isoformat()}))


def load(path: Path) -> dict | None:
    """{"token", "saved_at"}；檔案不存在或內容壞掉都當作沒有。"""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        token, saved = rec["token"], datetime.fromisoformat(rec["saved_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(token, str) or not token:
        return None
    return {"token": token, "saved_at": saved if saved.tzinfo else saved.replace(tzinfo=timezone.utc)}


def delete(path: Path) -> bool:
    with lock.locked(path):
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True


def status(path: Path, now: datetime | None = None) -> dict:
    """給頁面與 log 看的狀態，不含 token。state：missing / ok / expiring / expired。"""
    now = now or datetime.now(timezone.utc)
    rec = load(path)
    if rec is None:
        return {"state": "missing"}
    expires = rec["saved_at"] + timedelta(days=VALID_DAYS)
    left = expires - now
    state = "expired" if left.total_seconds() < 0 else "expiring" if left.days <= WARN_DAYS else "ok"
    return {"state": state, "tail": rec["token"][-4:],
            "saved_at": rec["saved_at"].date().isoformat(), "expires_at": expires.date().isoformat(),
            "days_left": left.days}


def resolve(path: Path) -> Auth | None:
    rec = load(path)
    return Auth("stored-token", rec["token"]) if rec is not None else None


def scrub_environ(environ: MutableMapping[str, str]) -> list[str]:
    """就地移除排在 OAuth token 前面的來源，回傳實際移掉的名稱。
    ClaudeAgentOptions(env=...) 只能新增或覆寫、不能移除，所以要在行程自己的環境裡移。"""
    removed = [name for name in SHADOWING_ENV if name in environ]
    for name in removed:
        del environ[name]
    return removed
