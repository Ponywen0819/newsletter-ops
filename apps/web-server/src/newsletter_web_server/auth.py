"""OAuth token 的驗證與儲存；最近的驗證結果讀寫 state/auth_health.json（定時驗證在 worker）。只限本機由 Handler 擋。"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from newsletter_shared import auth_health, auth_store

from .errors import ApiError

STALE_AFTER = timedelta(hours=26)  # worker 每天驗一次；超過這麼久沒更新＝worker 可能停了，不能再說「正常」


class Auth:
    def __init__(self, root: Path, checker: Callable[[str], dict] | None = None):
        self.token_path = auth_store.token_path(root)
        self.health_path = auth_health.health_path(root)
        self.checker = checker or (lambda token: auth_health.run_check(token, root))
        self._check_lock = threading.Lock()  # 同時只跑一個驗證

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
        auth_health.write(bool(result.get("ok")), str(result.get("kind", "other")), self.health_path)

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

    def health(self, now: datetime | None = None) -> dict:
        """給所有人看的粗略狀態，不含驗證訊息與 token（state 見 apps/web-server/README.md）。"""
        if auth_store.resolve(self.token_path) is None:
            return {"state": "missing", "kind": "missing", "checked_at": None}
        last = auth_health.read(self.health_path)
        if last is None:
            return {"state": "unknown", "kind": "unchecked", "checked_at": None}
        checked_at = last["at"].isoformat(timespec="seconds")
        if (now or datetime.now(timezone.utc)) - last["at"] > STALE_AFTER:
            return {"state": "unknown", "kind": "stale", "checked_at": checked_at}
        if last["ok"]:
            state = "expiring" if self.status()["state"] in ("expiring", "expired") else "ok"
        else:
            state = "invalid" if last["kind"] in ("auth", "api_key") else "unknown"
        return {"state": state, "kind": last["kind"], "checked_at": checked_at}

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
        self.health_path.unlink(missing_ok=True)
        return {"message": "已刪除儲存的 token" if removed else "本來就沒有儲存的 token", "status": self.status()}
