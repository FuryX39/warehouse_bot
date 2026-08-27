from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session


def coerce_sql_bool(value: object) -> bool:
    """SQLite stores Boolean as 0/1; Postgres drivers return True/False."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(int(value))
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def add_boolean_column_sql(dialect: str, table: str, column: str) -> str | None:
    if dialect == "sqlite":
        return f"ALTER TABLE {table} ADD COLUMN {column} BOOLEAN NOT NULL DEFAULT 0"
    if dialect == "postgresql":
        return (
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )
    return None


def create_db_engine(db_url: str):
    connect_args: dict[str, object] = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(db_url, future=True, connect_args=connect_args)
    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session(engine) -> Session:
    return Session(engine)
