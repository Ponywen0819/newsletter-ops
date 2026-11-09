#!/usr/bin/env python3
"""Claude 認證的最近驗證結果：state/auth_health.json（{ok, kind, at}）。worker 寫（每天定時、每日流程授權失敗時），
web 寫（/auth 測試或貼上 token 時）也讀（/api/auth-health）；兩邊只靠這個檔溝通。

用法：uv run --project packages/shared python -m newsletter_shared.auth_health --selftest
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from newsletter_shared import ROOT, atomic, auth_store

CHECK_TIMEOUT = 90  # 秒


def health_path(root: Path = ROOT) -> Path:
    return root / "state" / "auth_health.json"


def write(ok: bool, kind: str, path: Path, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    atomic.write_text(path, json.dumps({"ok": ok, "kind": kind, "at": now.isoformat(timespec="seconds")}))


def read(path: Path) -> dict | None:
    """{ok, kind, at（datetime）}；檔案不存在或壞掉都當作沒有。"""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        ok, kind, at = rec["ok"], rec["kind"], datetime.fromisoformat(rec["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(ok, bool) or not isinstance(kind, str):
        return None
    return {"ok": ok, "kind": kind, "at": at if at.tzinfo else at.replace(tzinfo=timezone.utc)}


def run_check(token: str | None = None, root: Path = ROOT) -> dict:
    """跑 `newsletter-llm --auth-check`（同一個 venv 要裝著 newsletter-llm），回傳 {ok, kind, message}。
    token 為 None 時驗證儲存的 token；有給就驗證它（走環境變數，不上命令列）。"""
    cmd = [sys.executable, "-m", "newsletter_llm.runner", "--auth-check"]
    env = dict(os.environ)
    if token is not None:
        cmd.append("--token-from-env")
        env[auth_store.TOKEN_ENV] = token
    try:
        p = subprocess.run(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                           timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "kind": "timeout", "message": f"驗證逾時（超過 {CHECK_TIMEOUT} 秒）"}
    except OSError as exc:
        return {"ok": False, "kind": "env", "message": f"無法啟動驗證程序：{exc.strerror or type(exc).__name__}"}
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and isinstance(result.get("ok"), bool):
            message = str(result.get("message", ""))
            return {"ok": result["ok"], "kind": str(result.get("kind", "other")),
                    "message": message.replace(token, "***") if token else message}
    if p.returncode == 2:  # newsletter-llm 在沒有 SDK 時、還沒走到驗證就以 2 結束
        return {"ok": False, "kind": "env", "message": "驗證需要 claude-agent-sdk（newsletter-llm）"}
    return {"ok": False, "kind": "other", "message": f"驗證程序異常結束（exit {p.returncode}）"}


def selftest() -> None:
    import tempfile
    from unittest import mock

    with tempfile.TemporaryDirectory() as d:
        path = health_path(Path(d))
        assert path == Path(d) / "state" / "auth_health.json" and read(path) is None
        now = datetime(2026, 11, 8, 7, 30, tzinfo=timezone.utc)
        write(False, "auth", path, now)
        assert read(path) == {"ok": False, "kind": "auth", "at": now}
        assert [p.name for p in path.parent.iterdir()] == [path.name]  # 原子寫入不留暫存檔
        for bad in ("", "{", '{"ok": "yes", "kind": "ok", "at": "2026-11-08T00:00:00"}', '{"ok": true}'):
            path.write_text(bad, encoding="utf-8")
            assert read(path) is None, bad

        secret = "sk-ant-oat01-" + "Qw7" * 12

        def fake(stdout="", code=0, raises=None):
            def run(cmd, **kw):
                fake.cmd, fake.env = cmd, kw["env"]
                if raises:
                    raise raises
                return subprocess.CompletedProcess(cmd, code, stdout, "")
            return run

        with mock.patch.object(subprocess, "run", fake('noise\n{"ok": false, "kind": "auth", "message": "bad ' + secret + '"}\n', 5)):
            res = run_check(secret)
        assert res == {"ok": False, "kind": "auth", "message": "bad ***"}, res
        assert fake.cmd[-2:] == ["--auth-check", "--token-from-env"] and fake.env[auth_store.TOKEN_ENV] == secret
        assert secret not in " ".join(fake.cmd)
        with mock.patch.object(subprocess, "run", fake('{"ok": true, "kind": "ok", "message": "驗證通過"}')):
            assert run_check()["ok"] is True
        assert fake.cmd[-1] == "--auth-check"  # 沒給 token：驗證儲存的
        with mock.patch.object(subprocess, "run", fake(code=2)):
            assert run_check()["kind"] == "env"
        with mock.patch.object(subprocess, "run", fake(code=1)):
            assert run_check()["kind"] == "other"
        with mock.patch.object(subprocess, "run", fake(raises=subprocess.TimeoutExpired("x", 1))):
            assert run_check()["kind"] == "timeout"
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
