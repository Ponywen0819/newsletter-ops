import subprocess
from datetime import datetime, timezone

import pytest

from newsletter_shared import auth_store
from newsletter_shared.auth_health import health_path, read, run_check, write

SECRET = "sk-ant-oat01-" + "Qw7" * 12


def test_write_read(tmp_path):
    path = health_path(tmp_path)
    assert path == tmp_path / "state" / "auth_health.json" and read(path) is None
    now = datetime(2026, 11, 8, 7, 30, tzinfo=timezone.utc)
    write(False, "auth", path, now)
    assert read(path) == {"ok": False, "kind": "auth", "at": now}
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # 原子寫入不留暫存檔


@pytest.mark.parametrize("bad", ["", "{", '{"ok": "yes", "kind": "ok", "at": "2026-11-08T00:00:00"}', '{"ok": true}'])
def test_read_rejects_bad(tmp_path, bad):
    path = tmp_path / "auth_health.json"
    path.write_text(bad, encoding="utf-8")
    assert read(path) is None


@pytest.fixture
def fake(monkeypatch):
    calls = {}

    def install(stdout="", code=0, raises=None):
        def run(cmd, **kw):
            calls["cmd"], calls["env"] = cmd, kw["env"]
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, code, stdout, "")
        monkeypatch.setattr(subprocess, "run", run)
        return calls
    return install


def test_check_masks_token(fake):
    calls = fake('noise\n{"ok": false, "kind": "auth", "message": "bad ' + SECRET + '"}\n', 5)
    assert run_check(SECRET) == {"ok": False, "kind": "auth", "message": "bad ***"}
    assert calls["cmd"][-2:] == ["--auth-check", "--token-from-env"] and calls["env"][auth_store.TOKEN_ENV] == SECRET
    assert SECRET not in " ".join(calls["cmd"])


def test_check_stored_token(fake):
    calls = fake('{"ok": true, "kind": "ok", "message": "驗證通過"}')
    assert run_check()["ok"] is True
    assert calls["cmd"][-1] == "--auth-check"


@pytest.mark.parametrize(("kw", "kind"), [
    ({"code": 2}, "env"),
    ({"code": 1}, "other"),
    ({"raises": subprocess.TimeoutExpired("x", 1)}, "timeout"),
])
def test_check_failures(fake, kw, kind):
    fake(**kw)
    assert run_check()["kind"] == kind
