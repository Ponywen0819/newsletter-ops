"""Site 丟出、Handler 轉成 HTTP 回應的錯誤。"""
from __future__ import annotations

from http import HTTPStatus


class NotFound(Exception):
    pass


class ApiError(Exception):
    """帶狀態碼的錯誤，Handler 轉成 {"error": message}。"""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
