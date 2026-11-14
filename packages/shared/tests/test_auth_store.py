import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from newsletter_shared.auth_store import (
    MAX_LEN, TOKEN_ENV, TOKEN_FILE_ENV, check_format, clean, delete, load, resolve, save, scrub_environ, status,
    token_path,
)

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
SECRET = "sk-ant-oat01-" + "Zx9" * 12


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / "oauth_token.json"


def private(path):
    """權限 600。Windows 沒有 POSIX 權限位（stat 只會回 666/444），只在 POSIX 上檢查。"""
    return os.name == "nt" or stat.S_IMODE(path.stat().st_mode) == 0o600


def files(path):
    return [p.name for p in path.parent.iterdir() if p.name != path.name + ".lock"]


def test_format():
    assert check_format("") and check_format("short") and check_format("x" * (MAX_LEN + 1))
    assert check_format("a" * 30 + " " + "b" * 30) and check_format("a" * 30 + "\n" + "b" * 30)
    assert check_format("sk-ant-oat01-" + "Ab1_-" * 10) is None
    assert clean("  tok\n") == "tok"


def test_save_load_status(path):
    assert load(path) is None and status(path, NOW) == {"state": "missing"}
    assert delete(path) is False
    save(SECRET, path, NOW)
    assert private(path)
    assert load(path)["token"] == SECRET
    assert files(path) == [path.name]
    st = status(path, NOW)
    assert st == {"state": "ok", "tail": SECRET[-4:], "saved_at": "2026-10-02",
                  "expires_at": "2027-10-02", "days_left": 365}
    assert SECRET not in json.dumps(st)
    assert delete(path) is True and not path.exists() and delete(path) is False


@pytest.mark.parametrize(("days", "state"), [(334, "ok"), (335, "expiring"), (364, "expiring"), (366, "expired")])
def test_expiry(path, days, state):
    save(SECRET, path, NOW)
    assert status(path, NOW + timedelta(days=days))["state"] == state


def test_overwrite_and_failed_write(path, monkeypatch):
    """覆寫：權限仍是 600；寫入失敗不能動到舊檔、也不留暫存檔。"""
    save(SECRET, path, NOW)
    save(SECRET + "2", path, NOW)
    assert load(path)["token"] == SECRET + "2" and private(path)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save("never-saved-" + "x" * 20, path, NOW)
    monkeypatch.undo()
    assert load(path)["token"] == SECRET + "2"
    assert files(path) == [path.name]


@pytest.mark.parametrize("bad", ["", "not json", "[]", '{"token": 1, "saved_at": "x"}', '{"token": "", "saved_at": "2026-01-01"}'])
def test_corrupt_is_missing(path, bad):
    path.parent.mkdir(parents=True)
    path.write_text(bad, encoding="utf-8")
    assert load(path) is None


def test_resolve_ignores_env(path, tmp_path, monkeypatch):
    """認證來源只有儲存的檔；環境變數（OAuth token、API key）一律不算。"""
    save(SECRET, path, NOW)
    none = tmp_path / "none.json"
    assert resolve(none) is None
    monkeypatch.setenv(TOKEN_ENV, "envtok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    assert resolve(none) is None
    got = resolve(path)
    assert got.source == "stored-token" and got.token == SECRET
    assert SECRET not in repr(got)


def test_token_path():
    assert token_path(Path("/r"), {}) == Path("/r/state/oauth_token.json")
    assert token_path(Path("/r"), {TOKEN_FILE_ENV: "/x/t.json"}) == Path("/x/t.json")


def test_scrub_environ():
    """排在 OAuth token 前面的來源整批移掉，其他變數與 OAuth token 本身不動。"""
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1",
           TOKEN_ENV: "tok", "PATH": "/bin", "ANTHROPIC_BASE_URL": "https://x"}
    assert scrub_environ(env) == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"]
    assert env == {TOKEN_ENV: "tok", "PATH": "/bin", "ANTHROPIC_BASE_URL": "https://x"}
    assert scrub_environ(env) == []
