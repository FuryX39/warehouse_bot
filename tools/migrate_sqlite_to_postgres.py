"""Копия трёх SQLite в одну Postgres (или другую пустую БД).

Не вызывает init_schema() на приёмнике: сиды заняли бы id до копирования.

Пример:
  python tools/migrate_sqlite_to_postgres.py \\
    --source-main sqlite:///crm_bot.db \\
    --source-movement sqlite:///movements.db \\
    --source-dealer sqlite:///dealer_analysis.db \\
    --dest postgresql+psycopg://warehouse:PASSWORD@127.0.0.1:5432/warehouse
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Boolean, MetaData, String, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import Table

from app.db import coerce_sql_bool, create_db_engine

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _as_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Пустой URL базы")
    if "://" in value:
        return value
    path = Path(value)
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return "sqlite:///" + path.as_posix()


def schema_groups() -> list[tuple[str, list[MetaData]]]:
    """Таблицы по исходным SQLite. Имена не пересекаются."""
    from app.catalog_repository import _Base as CatalogBase
    from app.crm_repository import _Base as CrmBase
    from app.dealer_analysis_repository import DealerAnalysisBase
    from app.fbs_packing_repository import _Base as FbsBase
    from app.movement_repository import MovementBase
    from app.ozon_fbo_supply_repository import _Base as FboBase
    from app.repositories import Base as InventoryBase
    from app.storage_warehouse_repository import _Base as StorageBase
    from app.warehouse_receipts_repository import _Base as ReceiptsBase
    from app.warehouse_roles_repository import _Base as RolesBase
    from app.warehouse_schedule_repository import _Base as ScheduleBase
    from app.warehouse_stock_repository import _Base as StockCacheBase
    from app.warehouse_task_summary_repository import _Base as TaskSummaryBase
    from app.warehouse_tasks_repository import _Base as TasksBase
    from app.warehouse_transfers_repository import _Base as TransfersBase
    from app.warehouse_users_repository import _Base as UsersBase
    from app.warehouse_writeoffs_repository import _Base as WriteoffsBase

    return [
        (
            "main",
            [
                InventoryBase.metadata,
                UsersBase.metadata,
                RolesBase.metadata,
                ScheduleBase.metadata,
                TaskSummaryBase.metadata,
                CrmBase.metadata,
                StorageBase.metadata,
                CatalogBase.metadata,
                StockCacheBase.metadata,
                ReceiptsBase.metadata,
                WriteoffsBase.metadata,
                TransfersBase.metadata,
                TasksBase.metadata,
                FbsBase.metadata,
                FboBase.metadata,
            ],
        ),
        ("movement", [MovementBase.metadata]),
        ("dealer", [DealerAnalysisBase.metadata]),
    ]


def tables_in_fk_order(metadatas: Iterable[MetaData]) -> list[Table]:
    tables: dict[str, Table] = {}
    for metadata in metadatas:
        for table in metadata.tables.values():
            tables[table.name] = table
    pending = set(tables)
    ordered: list[Table] = []
    while pending:
        progress = False
        for name in sorted(pending):
            table = tables[name]
            deps = {fk.column.table.name for fk in table.foreign_keys}
            if all(dep == name or dep not in pending for dep in deps):
                ordered.append(table)
                pending.remove(name)
                progress = True
        if not progress:
            for name in sorted(pending):
                ordered.append(tables[name])
            break
    return ordered


def checkpoint_sqlite(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")


def dest_row_count(engine: Engine, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)


def assert_dest_empty(engine: Engine, tables: list[Table]) -> None:
    nonempty = [t.name for t in tables if dest_row_count(engine, t) > 0]
    if nonempty:
        raise SystemExit(
            "Приёмник не пустой: " + ", ".join(nonempty[:20]) + ". Используйте пустую базу."
        )


def widen_oversize_strings(src: Engine, dest: Engine, table: Table) -> list[str]:
    if dest.dialect.name != "postgresql":
        return []
    changed: list[str] = []
    with src.connect() as conn:
        for col in table.columns:
            if not isinstance(col.type, String) or not col.type.length:
                continue
            max_len = conn.execute(select(func.max(func.length(col)))).scalar()
            if not max_len or int(max_len) <= int(col.type.length):
                continue
            width = int(max_len)
            with dest.begin() as dest_conn:
                dest_conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" ALTER COLUMN "{col.name}" '
                        f"TYPE VARCHAR({width})"
                    )
                )
            changed.append(f"{table.name}.{col.name}:{col.type.length}->{width}")
    return changed


def coerce_row(table: Table, mapping: dict) -> dict:
    row: dict = {}
    for col in table.columns:
        value = mapping.get(col.name)
        if isinstance(col.type, Boolean):
            value = coerce_sql_bool(value)
        row[col.name] = value
    return row


def copy_table(src: Engine, dest: Engine, table: Table) -> int:
    if table.name not in inspect(src).get_table_names():
        return 0
    widen_oversize_strings(src, dest, table)
    col_count = max(1, len(table.columns))
    chunk = 50 if dest.dialect.name == "postgresql" else max(1, 800 // col_count)
    copied = 0
    with src.connect() as src_conn, dest.begin() as dest_conn:
        result = src_conn.execute(select(table))
        while True:
            raw = result.fetchmany(chunk)
            if not raw:
                break
            batch = [coerce_row(table, dict(item._mapping)) for item in raw]
            dest_conn.execute(table.insert(), batch)
            copied += len(batch)
    return copied


def reset_postgres_sequences(engine: Engine, tables: list[Table]) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in tables:
            pk_cols = [c for c in table.primary_key.columns if c.autoincrement]
            if len(pk_cols) != 1:
                continue
            col = pk_cols[0]
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": table.name, "column": col.name},
            ).scalar()
            if not seq:
                continue
            max_id = conn.execute(
                text(f'SELECT COALESCE(MAX("{col.name}"), 0) FROM "{table.name}"')
            ).scalar()
            max_id = int(max_id or 0)
            if max_id <= 0:
                continue
            conn.execute(text("SELECT setval(:seq, :val)"), {"seq": seq, "val": max_id})


def verify_counts(sources: dict[str, Engine], dest: Engine, groups: list[tuple[str, list[Table]]]) -> None:
    mismatches: list[str] = []
    for source_key, tables in groups:
        src = sources[source_key]
        src_names = set(inspect(src).get_table_names()) if src is not None else set()
        for table in tables:
            dest_count = dest_row_count(dest, table)
            if table.name not in src_names:
                src_count = 0
            else:
                with src.connect() as conn:
                    src_count = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
            if src_count != dest_count:
                mismatches.append(f"{table.name}: sqlite={src_count} dest={dest_count}")
    if mismatches:
        raise SystemExit("COUNT не совпал:\n  " + "\n  ".join(mismatches))


def create_dest_schema(engine: Engine) -> None:
    for _key, metadatas in schema_groups():
        for metadata in metadatas:
            metadata.create_all(engine)


def grouped_tables() -> list[tuple[str, list[Table]]]:
    return [(key, tables_in_fk_order(metadatas)) for key, metadatas in schema_groups()]


def migrate(
    *,
    source_main: str,
    source_movement: str,
    source_dealer: str,
    dest: str,
) -> dict[str, int]:
    from app.catalog_repository import CatalogRepository

    sources = {
        "main": create_db_engine(source_main),
        "movement": create_db_engine(source_movement),
        "dealer": create_db_engine(source_dealer),
    }
    dest_engine = create_db_engine(dest)
    for engine in sources.values():
        checkpoint_sqlite(engine)
    CatalogRepository(source_main)._cleanup_orphan_product_rows()

    groups = grouped_tables()
    all_tables = [table for _key, tables in groups for table in tables]
    create_dest_schema(dest_engine)
    assert_dest_empty(dest_engine, all_tables)

    copied: dict[str, int] = {}
    for source_key, tables in groups:
        src = sources[source_key]
        for table in tables:
            copied[table.name] = copy_table(src, dest_engine, table)
    reset_postgres_sequences(dest_engine, all_tables)
    verify_counts(sources, dest_engine, groups)
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Копия SQLite → Postgres без сидов init_schema")
    parser.add_argument("--source-main", default="sqlite:///crm_bot.db")
    parser.add_argument("--source-movement", default="sqlite:///movements.db")
    parser.add_argument("--source-dealer", default="sqlite:///dealer_analysis.db")
    parser.add_argument("--dest", required=True, help="postgresql+psycopg://…")
    args = parser.parse_args(argv)
    copied = migrate(
        source_main=_as_url(args.source_main),
        source_movement=_as_url(args.source_movement),
        source_dealer=_as_url(args.source_dealer),
        dest=_as_url(args.dest),
    )
    total = sum(copied.values())
    print(f"Скопировано строк: {total} в {len(copied)} таблицах")
    return 0


if __name__ == "__main__":
    sys.exit(main())
