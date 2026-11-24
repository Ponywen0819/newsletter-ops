"""測試用的 Postgres：每個測試一個暫時的 schema，跑完刪掉。只給各模組的 tests/conftest.py 用，正式程式不要 import。

用法：tests/conftest.py 寫 `pytest_plugins = ("newsletter_shared.pg_testing",)`，測試要資料庫時拿 `pg` fixture，
NEWSLETTER_DATABASE_URL 會指到一個空的 schema（search_path 已經設好）。

連線字串放 NEWSLETTER_TEST_DATABASE_URL（例如 postgresql://postgres:test@localhost:55432/newsletter_test）。
沒設時跳過需要資料庫的測試；CI（環境變數 CI 有值）沒設則直接失敗，免得整批測試默默被跳過。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from newsletter_shared.kg_store import ENV

TEST_ENV = "NEWSLETTER_TEST_DATABASE_URL"


@contextmanager
def temp_schema() -> Iterator[str]:
    """建一個空 schema，yield 預設就在這個 schema 的連線字串；結束時連同裡面的表一起刪掉。"""
    base = os.environ.get(TEST_ENV)
    if not base:
        if os.environ.get("CI"):
            pytest.fail(f"CI 必須設定 {TEST_ENV}")
        pytest.skip(f"沒設 {TEST_ENV}，跳過需要 Postgres 的測試")
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as con:
        con.execute(f"CREATE SCHEMA {name}")
    try:
        yield make_conninfo(base, options=f"-c search_path={name}")
    finally:
        with psycopg.connect(base, autocommit=True) as con:
            con.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def pg(monkeypatch) -> Iterator[str]:
    """NEWSLETTER_DATABASE_URL 指到一個暫時的空 schema。"""
    with temp_schema() as url:
        monkeypatch.setenv(ENV, url)
        yield url
