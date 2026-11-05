"""OAuth token 的驗證、儲存與定時檢查。只限本機由 Handler 擋。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from newsletter_shared import ROOT, auth_store

from .errors import ApiError

CHECK_TIMEOUT = 90  # 秒
CHECK_INTERVAL = 3600  # 秒


def run_auth_check(token: str) -> dict:
    """用 `newsletter-agent --auth-check` 驗證 token，回傳 {ok, kind, message}。token 走環境變數，不上命令列。"""
    cmd = [sys.executable, "-m", "newsletter_agent.agent_run", "--auth-check", "--token-from-env"]
    env = {**os.environ, auth_store.TOKEN_ENV: token}
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
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
            return {"ok": result["ok"], "kind": str(result.get("kind", "other")),
                    "message": str(result.get("message", "")).replace(token, "***")}
    if p.returncode == 2:  # agent_run 在沒有 SDK 時、還沒走到驗證就以 2 結束
        return {"ok": False, "kind": "env", "message": "驗證需要 claude-agent-sdk，請改用 `uv run --project apps/web-server newsletter-web` 啟動"}
    return {"ok": False, "kind": "other", "message": f"驗證程序異常結束（exit {p.returncode}）"}


class Auth:
    def __init__(self, root: Path, checker: Callable[[str], dict] | None = None):
        self.token_path = auth_store.token_path(root)
        self.checker = checker or run_auth_check
        self._check_lock = threading.Lock()  # 同時只跑一個驗證
        self.last_check: dict | None = None  # {ok, kind, at}


    def status(self) -> dict:
        """給前端畫的狀態，不含 token（最多尾 4 碼）。state：missing / ok / expiring / expired。"""
        return auth_store.status(self.token_path)

    def _failure(self, result: dict) -> ApiError:
        kind, msg = result.get("kind"), result.get("message", "")
        if kind == "timeout":
            return ApiError(HTTPStatus.GATEWAY_TIMEOUT, msg)
        if kind == "env":
            return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, msg)
        if kind == "auth":
            return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"授權失敗：{msg}（token 可能貼錯、已撤銷或已過期）")
        if kind == "quota":
            return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"額度用完，現在無法驗證：{msg}（稍後再試）")
        return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"驗證失敗：{msg}")

    def _record(self, result: dict) -> None:
        self.last_check = {"ok": bool(result.get("ok")), "kind": str(result.get("kind", "other")),
                           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def _verify(self, token: str, stored: bool = False) -> None:
        """跑一次驗證；沒通過就丟 ApiError。stored：驗的是已儲存的 token。"""
        if not self._check_lock.acquire(blocking=False):
            raise ApiError(HTTPStatus.CONFLICT, "另一個驗證正在進行，請稍候再試")
        try:
            result = self.checker(token)
        finally:
            self._check_lock.release()
        if stored or result.get("ok"):  # 新 token 沒通過時，生效的仍是舊的
            self._record(result)
        if not result.get("ok"):
            raise self._failure(result)

    def check_now(self) -> None:
        """用儲存的 token 驗證一次，結果記在 last_check。沒有儲存的 token 就清掉。"""
        auth = auth_store.resolve(self.token_path)
        if auth is None:
            self.last_check = None
        elif self._check_lock.acquire(blocking=False):
            try:
                result = self.checker(auth.token)
            finally:
                self._check_lock.release()
            self._record(result)

    def monitor(self, interval: float, stop: threading.Event) -> None:
        """背景執行緒：先驗證一次，之後每 interval 秒一次，直到 stop。"""
        while True:
            try:
                self.check_now()
            except Exception:
                traceback.print_exc()
            if stop.wait(interval):
                return

    def health(self) -> dict:
        """給所有人看的粗略狀態，不含驗證訊息與 token（state 見 apps/web-server/README.md）。"""
        if auth_store.resolve(self.token_path) is None:
            return {"state": "missing", "kind": "missing", "checked_at": None}
        last = self.last_check
        if last is None:
            return {"state": "unknown", "kind": "unchecked", "checked_at": None}
        if last["ok"]:
            state = "expiring" if self.status()["state"] in ("expiring", "expired") else "ok"
        else:
            state = "invalid" if last["kind"] in ("auth", "api_key") else "unknown"
        return {"state": state, "kind": last["kind"], "checked_at": last["at"]}

    def submit_token(self, raw: str) -> dict:
        """貼上新的 token：先驗證，通過才覆寫。無效的 token 不會洗掉原本可用的。"""
        token = auth_store.clean(raw)
        if (problem := auth_store.check_format(token)) is not None:
            raise ApiError(HTTPStatus.BAD_REQUEST, problem)
        self._verify(token)
        auth_store.save(token, self.token_path)
        return {"message": "驗證通過，已儲存", "status": self.status()}

    def test_token(self) -> dict:
        rec = auth_store.load(self.token_path)
        if rec is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "還沒有儲存的 token")
        self._verify(rec["token"], stored=True)
        return {"message": "連線正常", "status": self.status()}

    def revoke_token(self) -> dict:
        """只刪掉這裡儲存的檔；token 在 Anthropic 端仍然有效。"""
        removed = auth_store.delete(self.token_path)
        self.last_check = None
        return {"message": "已刪除儲存的 token" if removed else "本來就沒有儲存的 token", "status": self.status()}
