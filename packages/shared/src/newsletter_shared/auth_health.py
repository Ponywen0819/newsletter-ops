#!/usr/bin/env python3
"""Claude 認證的最近驗證結果：state/auth_health.json（{ok, kind, at}）。worker 寫（每天定時、每日流程授權失敗時），
web 寫（/auth 測試或貼上 token 時）也讀（/api/auth-health）；兩邊只靠這個檔溝通。
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
    env = {**os.environ, "PYTHONUTF8": "1"}  # 子程序的 stdout 一律 UTF-8（Windows 預設是系統編碼）
    if token is not None:
        cmd.append("--token-from-env")
        env[auth_store.TOKEN_ENV] = token
    try:
        p = subprocess.run(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", check=False,
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
