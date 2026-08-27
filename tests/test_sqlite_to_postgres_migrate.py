"""Подготовка к Postgres: bool, ALTER и копия SQLite→SQLite тем же скриптом."""

from __future__ import annotations

from app.db import add_boolean_column_sql, coerce_sql_bool
from app.repositories import InventoryRepository
from tools.migrate_sqlite_to_postgres import migrate


def test_coerce_sql_bool() -> None:
    assert coerce_sql_bool(0) is False
    assert coerce_sql_bool(1) is True
    assert coerce_sql_bool(False) is False
    assert coerce_sql_bool(True) is True
    assert coerce_sql_bool("t") is True
    assert coerce_sql_bool(None) is False


def test_add_boolean_column_sql_dialects() -> None:
    sqlite_sql = add_boolean_column_sql("sqlite", "t", "flag")
    pg_sql = add_boolean_column_sql("postgresql", "t", "flag")
    assert sqlite_sql is not None and "DEFAULT 0" in sqlite_sql
    assert pg_sql is not None and "DEFAULT FALSE" in pg_sql
    assert "IF NOT EXISTS" in pg_sql
    assert add_boolean_column_sql("mysql", "t", "flag") is None


def test_migrate_copies_inventory_between_sqlite_files(tmp_path) -> None:
    src_main = f"sqlite:///{(tmp_path / 'src_main.db').as_posix()}"
    src_mov = f"sqlite:///{(tmp_path / 'src_mov.db').as_posix()}"
    src_dealer = f"sqlite:///{(tmp_path / 'src_dealer.db').as_posix()}"
    dest = f"sqlite:///{(tmp_path / 'dest.db').as_posix()}"

    repo = InventoryRepository(src_main)
    repo.init_schema()
    repo.upsert_stock("SKU-1", 9)

    copied = migrate(
        source_main=src_main,
        source_movement=src_mov,
        source_dealer=src_dealer,
        dest=dest,
    )
    assert copied.get("product_stocks") == 1

    dest_repo = InventoryRepository(dest)
    available = dest_repo.get_available_stock_map()
    assert available.get("SKU-1") == 9
