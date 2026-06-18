from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session


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
            cursor.close()

    return engine


def create_session(engine) -> Session:
    return Session(engine)
