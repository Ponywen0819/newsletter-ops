#!/usr/bin/env python3
"""OAuth token 的儲存與讀取，newsletter-web 與 agent_run.py 共用。

用法：uv run --project packages/shared python -m newsletter_shared.auth_store --selftest

token 唯一的來源是 state/oauth_token.json（/auth 貼上後存的，權限 600），不讀 CLAUDE_CODE_OAUTH_TOKEN：
換新要立刻生效，所有程序看到同一份。不支援 API key；scrub_environ() 移除優先序高於 OAuth 的來源。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, MutableMapping

from newsletter_shared import lock
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
    """寫成權限 600 的暫存檔再 os.replace，讀的人不會看到半寫入的內容。"""
    now = now or datetime.now(timezone.utc)
    payload = json.dumps({"token": token, "saved_at": now.isoformat()})
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock.locked(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")  # mkstemp 建的檔是 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise


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


def selftest() -> None:
    import stat
    import tempfile as tf
    from unittest import mock

    # 格式
    assert check_format("") and check_format("short") and check_format("x" * (MAX_LEN + 1))
    assert check_format("a" * 30 + " " + "b" * 30) and check_format("a" * 30 + "\n" + "b" * 30)
    assert check_format("sk-ant-oat01-" + "Ab1_-" * 10) is None
    assert clean("  tok\n") == "tok"

    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    secret = "sk-ant-oat01-" + "Zx9" * 12
    with tf.TemporaryDirectory() as d:
        path = Path(d) / "state" / "oauth_token.json"
        assert load(path) is None and status(path, now) == {"state": "missing"}
        assert delete(path) is False

        save(secret, path, now)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, oct(path.stat().st_mode)
        assert load(path)["token"] == secret
        assert [p.name for p in path.parent.iterdir() if p.name != path.name + ".lock"] == [path.name]
        st = status(path, now)
        assert st == {"state": "ok", "tail": secret[-4:], "saved_at": "2026-10-02",
                      "expires_at": "2027-10-02", "days_left": 365}, st
        assert secret not in json.dumps(st)

        # 到期狀態
        assert status(path, now + timedelta(days=334))["state"] == "ok"
        assert status(path, now + timedelta(days=335))["state"] == "expiring"
        assert status(path, now + timedelta(days=364))["state"] == "expiring"
        assert status(path, now + timedelta(days=366))["state"] == "expired"

        # 覆寫：權限仍是 600；寫入失敗不能動到舊檔、也不留暫存檔
        save(secret + "2", path, now)
        assert load(path)["token"] == secret + "2" and stat.S_IMODE(path.stat().st_mode) == 0o600
        real_replace = os.replace
        try:
            def boom(*a, **k):
                raise OSError("disk full")
            os.replace = boom  # type: ignore[assignment]
            try:
                save("never-saved-" + "x" * 20, path, now)
                raise AssertionError("應該丟 OSError")
            except OSError:
                pass
        finally:
            os.replace = real_replace  # type: ignore[assignment]
        assert load(path)["token"] == secret + "2"
        assert [p.name for p in path.parent.iterdir() if p.name != path.name + ".lock"] == [path.name]

        # 內容壞掉＝當作沒有，不崩潰
        for bad in ("", "not json", "[]", '{"token": 1, "saved_at": "x"}', '{"token": "", "saved_at": "2026-01-01"}'):
            path.write_text(bad, encoding="utf-8")
            assert load(path) is None, bad
        save(secret, path, now)

        # 認證來源只有儲存的檔；環境變數（OAuth token、API key）一律不算
        none = Path(d) / "none.json"
        assert resolve(none) is None
        with mock.patch.dict(os.environ, {TOKEN_ENV: "envtok", "ANTHROPIC_API_KEY": "sk-ant-api03-x"}):
            assert resolve(none) is None
            got = resolve(path)
            assert got.source == "stored-token" and got.token == secret
        assert secret not in repr(got)

        # 路徑可由環境變數改
        assert token_path(Path("/r"), {}) == Path("/r/state/oauth_token.json")
        assert token_path(Path("/r"), {TOKEN_FILE_ENV: "/x/t.json"}) == Path("/x/t.json")

        assert delete(path) is True and not path.exists() and delete(path) is False

    # 排在 OAuth token 前面的來源整批移掉，其他變數與 OAuth token 本身不動
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1",
           TOKEN_ENV: "tok", "PATH": "/bin", "ANTHROPIC_BASE_URL": "https://x"}
    assert scrub_environ(env) == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"]
    assert env == {TOKEN_ENV: "tok", "PATH": "/bin", "ANTHROPIC_BASE_URL": "https://x"}
    assert scrub_environ(env) == []
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        print("用法：python -m newsletter_shared.auth_store --selftest", file=sys.stderr)
        raise SystemExit(2)
