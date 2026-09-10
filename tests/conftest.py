"""Общие фикстуры pytest: изолированная PostgreSQL-схема на каждый тест."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")


def postgres_base_url() -> str | None:
    """Базовый URL Postgres для тестов (без #schema=)."""
    raw = (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DB_URL") or "").strip()
    if raw.startswith("postgresql"):
        return raw.split("#", 1)[0]
    return None


@pytest.fixture
def db_url() -> str:
    """
    Отдельная схема PostgreSQL на тест; после теста схема удаляется.

    Требует TEST_DATABASE_URL или DB_URL=postgresql+psycopg://…
    """
    base = postgres_base_url()
    if not base:
        pytest.skip(
            "PostgreSQL required for tests: set TEST_DATABASE_URL or DB_URL "
            "to postgresql+psycopg://…"
        )
    schema = f"pytest_{uuid.uuid4().hex}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield f"{base}#schema={schema}"
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
