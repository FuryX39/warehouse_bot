"""Склады хранения (единицы учёта остатков в новой панели)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, delete, func, inspect, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

_DEFAULT_WAREHOUSE_NAME = "Основной склад"
_DEFAULT_WAREHOUSE_CODE = "MAIN"
_DEFAULT_BIN_CODE = "MAIN"
_DEFAULT_BIN_NAME = "Основная"
_LEGACY_WAREHOUSE_NAME = "Старый склад"
_LEGACY_WAREHOUSE_CODE = "LEGACY"
_LEGACY_STOCK_MIGRATION_KEY = "legacy_warehouse_stock_v1"
_BINS_STOCK_MIGRATION_KEY = "storage_bins_stock_v1"


class _Base(DeclarativeBase):
    pass


class StorageWarehouseGroup(_Base):
    __tablename__ = "storage_warehouse_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StorageWarehouse(_Base):
    __tablename__ = "storage_warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    address_comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("storage_warehouse_groups.id"), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StorageBin(_Base):
    """Ячейка склада. На каждом складе есть MAIN («Основная»)."""

    __tablename__ = "storage_bins"
    __table_args__ = (UniqueConstraint("warehouse_id", "code", name="uq_storage_bin_wh_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storage_warehouses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StorageStock(_Base):
    """Остатки SKU в ячейке склада."""

    __tablename__ = "storage_stocks"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "bin_id", "sku", name="uq_storage_stock_wh_bin_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storage_warehouses.id", ondelete="CASCADE"), nullable=False
    )
    bin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storage_bins.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class InsufficientBinStockError(ValueError):
    """В ячейке меньше единиц, чем требует списание."""


@dataclass
class StorageBinRow:
    id: int
    warehouse_id: int
    code: str
    name: str
    is_default: bool
    sku_count: int
    total_stock: int
    created_at_ts: int


@dataclass
class StorageWarehouseRow:
    id: int
    name: str
    address: str
    address_comment: str
    comment: str
    code: str
    group_id: Optional[int]
    group_name: str
    is_default: bool
    sku_count: int
    total_stock: int
    created_at_ts: int
    updated_at_ts: int


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


class StorageWarehouseRepository:
    def __init__(self, db_url: str) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self._on_sku_changed: Callable[[set[str]], None] | None = None

    def set_stock_balance_hook(self, on_sku_changed: Callable[[set[str]], None] | None) -> None:
        self._on_sku_changed = on_sku_changed

    def get_default_warehouse_id(self) -> int | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(StorageWarehouse).where(StorageWarehouse.is_default.is_(True)).limit(1)
            )
            if row is None:
                row = session.scalar(select(StorageWarehouse).order_by(StorageWarehouse.id).limit(1))
            return int(row.id) if row is not None else None

    def get_legacy_warehouse_id(self) -> int | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(StorageWarehouse)
                .where(StorageWarehouse.code == _LEGACY_WAREHOUSE_CODE)
                .limit(1)
            )
            return int(row.id) if row is not None else None

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._ensure_default_warehouse()
        self._migrate_bins_and_stock_unique()
        self._migrate_legacy_warehouse_stocks()

    def _ensure_default_warehouse(self) -> None:
        now = int(time.time())
        with Session(self.engine) as session:
            count = int(session.scalar(select(func.count()).select_from(StorageWarehouse)) or 0)
            if count > 0:
                return
            row = StorageWarehouse(
                name=_DEFAULT_WAREHOUSE_NAME,
                code=_DEFAULT_WAREHOUSE_CODE,
                is_default=True,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.flush()
            self._ensure_default_bin(session, int(row.id))
            session.commit()

    def _ensure_default_bin(self, session: Session, warehouse_id: int) -> StorageBin:
        row = session.scalar(
            select(StorageBin).where(
                StorageBin.warehouse_id == int(warehouse_id),
                StorageBin.is_default.is_(True),
            ).limit(1)
        )
        if row is None:
            row = session.scalar(
                select(StorageBin).where(
                    StorageBin.warehouse_id == int(warehouse_id),
                    StorageBin.code == _DEFAULT_BIN_CODE,
                ).limit(1)
            )
        if row is not None:
            if not row.is_default:
                row.is_default = True
            return row
        now = int(time.time())
        row = StorageBin(
            warehouse_id=int(warehouse_id),
            code=_DEFAULT_BIN_CODE,
            name=_DEFAULT_BIN_NAME,
            is_default=True,
            created_at_ts=now,
        )
        session.add(row)
        session.flush()
        return row

    def get_default_bin_id(self, warehouse_id: int) -> int | None:
        with Session(self.engine) as session:
            if session.get(StorageWarehouse, int(warehouse_id)) is None:
                return None
            row = self._ensure_default_bin(session, int(warehouse_id))
            session.commit()
            return int(row.id)

    def resolve_bin_id(self, warehouse_id: int, bin_id: Any = None) -> int:
        with Session(self.engine) as session:
            if session.get(StorageWarehouse, int(warehouse_id)) is None:
                raise ValueError("Склад не найден")
            raw: int | None
            try:
                raw = int(bin_id) if bin_id not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise ValueError("Некорректная ячейка") from exc
            resolved = self._resolve_bin_id(session, int(warehouse_id), raw)
            session.commit()
            return int(resolved)

    def _sync_state_done(self, session: Session, key: str) -> bool:
        if "sync_state" not in inspect(self.engine).get_table_names():
            return False
        row = session.execute(
            text("SELECT value_int FROM sync_state WHERE key = :key"),
            {"key": key},
        ).fetchone()
        return bool(row and int(row[0]) == 1)

    def _mark_sync_state_done(self, session: Session, key: str) -> None:
        if "sync_state" not in inspect(self.engine).get_table_names():
            return
        existing = session.execute(
            text("SELECT key FROM sync_state WHERE key = :key"),
            {"key": key},
        ).fetchone()
        if existing:
            session.execute(
                text("UPDATE sync_state SET value_int = 1 WHERE key = :key"),
                {"key": key},
            )
        else:
            session.execute(
                text("INSERT INTO sync_state (key, value_int) VALUES (:key, 1)"),
                {"key": key},
            )

    def _migrate_bins_and_stock_unique(self) -> None:
        """Ячейки MAIN + остаток (склад+ячейка+SKU). Идемпотентно для SQLite и Postgres."""
        insp = inspect(self.engine)
        dialect = self.engine.dialect.name
        tables = set(insp.get_table_names())
        if "storage_warehouses" not in tables:
            return
        with Session(self.engine) as session:
            if "storage_bins" not in tables:
                StorageBin.__table__.create(self.engine, checkfirst=True)
            warehouses = session.scalars(select(StorageWarehouse)).all()
            default_bin_by_wh: dict[int, int] = {}
            for wh in warehouses:
                bin_row = self._ensure_default_bin(session, int(wh.id))
                default_bin_by_wh[int(wh.id)] = int(bin_row.id)
            session.flush()

            if "storage_stocks" not in inspect(self.engine).get_table_names():
                session.commit()
                return
            cols = {c["name"] for c in inspect(self.engine).get_columns("storage_stocks")}
            if "bin_id" not in cols:
                session.execute(text("ALTER TABLE storage_stocks ADD COLUMN bin_id INTEGER"))
                session.flush()
                for wh_id, bin_id in default_bin_by_wh.items():
                    session.execute(
                        text(
                            "UPDATE storage_stocks SET bin_id = :bin_id "
                            "WHERE warehouse_id = :wh_id AND (bin_id IS NULL OR bin_id = 0)"
                        ),
                        {"bin_id": bin_id, "wh_id": wh_id},
                    )
                leftover = session.execute(
                    text("SELECT DISTINCT warehouse_id FROM storage_stocks WHERE bin_id IS NULL")
                ).all()
                for (wh_id,) in leftover:
                    bin_row = self._ensure_default_bin(session, int(wh_id))
                    session.execute(
                        text(
                            "UPDATE storage_stocks SET bin_id = :bin_id "
                            "WHERE warehouse_id = :wh_id AND bin_id IS NULL"
                        ),
                        {"bin_id": int(bin_row.id), "wh_id": int(wh_id)},
                    )
                session.flush()

            indexes = inspect(self.engine).get_indexes("storage_stocks")
            uniques = inspect(self.engine).get_unique_constraints("storage_stocks")
            old_uq = any(u.get("name") == "uq_storage_stock_wh_sku" for u in uniques)
            if not old_uq:
                for idx in indexes:
                    cols_idx = list(idx.get("column_names") or [])
                    if idx.get("unique") and cols_idx == ["warehouse_id", "sku"]:
                        old_uq = True
                        break
            new_uq = any(u.get("name") == "uq_storage_stock_wh_bin_sku" for u in uniques)
            if old_uq and dialect == "postgresql":
                session.execute(text("ALTER TABLE storage_stocks DROP CONSTRAINT IF EXISTS uq_storage_stock_wh_sku"))
                if not new_uq:
                    session.execute(
                        text(
                            "ALTER TABLE storage_stocks ADD CONSTRAINT uq_storage_stock_wh_bin_sku "
                            "UNIQUE (warehouse_id, bin_id, sku)"
                        )
                    )
            elif old_uq and dialect == "sqlite":
                session.commit()
                self._rebuild_sqlite_storage_stocks()
                return
            elif dialect == "postgresql" and not new_uq:
                try:
                    session.execute(
                        text(
                            "ALTER TABLE storage_stocks ADD CONSTRAINT uq_storage_stock_wh_bin_sku "
                            "UNIQUE (warehouse_id, bin_id, sku)"
                        )
                    )
                except Exception:
                    session.rollback()
                    with Session(self.engine) as retry:
                        retry.commit()
            self._mark_sync_state_done(session, _BINS_STOCK_MIGRATION_KEY)
            session.commit()

    def _rebuild_sqlite_storage_stocks(self) -> None:
        with Session(self.engine) as session:
            session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS storage_stocks_bins_new ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "warehouse_id INTEGER NOT NULL, "
                    "bin_id INTEGER NOT NULL, "
                    "sku VARCHAR(128) NOT NULL, "
                    "stock INTEGER NOT NULL DEFAULT 0, "
                    "CONSTRAINT uq_storage_stock_wh_bin_sku UNIQUE (warehouse_id, bin_id, sku)"
                    ")"
                )
            )
            session.execute(
                text(
                    "INSERT INTO storage_stocks_bins_new (id, warehouse_id, bin_id, sku, stock) "
                    "SELECT id, warehouse_id, bin_id, sku, stock FROM storage_stocks "
                    "WHERE bin_id IS NOT NULL"
                )
            )
            session.execute(text("DROP TABLE storage_stocks"))
            session.execute(text("ALTER TABLE storage_stocks_bins_new RENAME TO storage_stocks"))
            self._mark_sync_state_done(session, _BINS_STOCK_MIGRATION_KEY)
            session.commit()

    def _legacy_migration_done(self, session: Session) -> bool:
        if "sync_state" not in inspect(self.engine).get_table_names():
            return False
        row = session.execute(
            text("SELECT value_int FROM sync_state WHERE key = :key"),
            {"key": _LEGACY_STOCK_MIGRATION_KEY},
        ).fetchone()
        return bool(row and int(row[0]) == 1)

    def _mark_legacy_migration_done(self, session: Session) -> None:
        if "sync_state" not in inspect(self.engine).get_table_names():
            return
        existing = session.execute(
            text("SELECT key FROM sync_state WHERE key = :key"),
            {"key": _LEGACY_STOCK_MIGRATION_KEY},
        ).fetchone()
        if existing:
            session.execute(
                text("UPDATE sync_state SET value_int = 1 WHERE key = :key"),
                {"key": _LEGACY_STOCK_MIGRATION_KEY},
            )
        else:
            session.execute(
                text("INSERT INTO sync_state (key, value_int) VALUES (:key, 1)"),
                {"key": _LEGACY_STOCK_MIGRATION_KEY},
            )

    def _ensure_legacy_warehouse(self, session: Session) -> StorageWarehouse:
        row = session.scalar(
            select(StorageWarehouse).where(StorageWarehouse.code == _LEGACY_WAREHOUSE_CODE).limit(1)
        )
        if row is not None:
            if row.name != _LEGACY_WAREHOUSE_NAME:
                row.name = _LEGACY_WAREHOUSE_NAME
            self._ensure_default_bin(session, int(row.id))
            return row
        now = int(time.time())
        row = StorageWarehouse(
            name=_LEGACY_WAREHOUSE_NAME,
            code=_LEGACY_WAREHOUSE_CODE,
            is_default=False,
            created_at_ts=now,
            updated_at_ts=now,
        )
        session.add(row)
        session.flush()
        self._ensure_default_bin(session, int(row.id))
        return row

    def _migrate_legacy_warehouse_stocks(self) -> None:
        """Переносит остатки старой панели (product_stocks) на склад «Старый склад»."""
        with Session(self.engine) as session:
            if self._legacy_migration_done(session):
                return
            legacy_wh = self._ensure_legacy_warehouse(session)
            legacy_id = int(legacy_wh.id)

            stocks_by_sku: dict[str, int] = {}
            if "product_stocks" in inspect(self.engine).get_table_names():
                for sku, stock in session.execute(
                    text("SELECT sku, stock FROM product_stocks WHERE stock != 0")
                ).all():
                    sku_s = str(sku or "").strip()
                    if not sku_s:
                        continue
                    qty = max(0, int(stock or 0))
                    if qty:
                        stocks_by_sku[sku_s] = qty

            if not stocks_by_sku:
                default_wh = session.scalar(
                    select(StorageWarehouse).where(StorageWarehouse.is_default.is_(True)).limit(1)
                )
                if default_wh is not None and int(default_wh.id) != legacy_id:
                    rows = session.scalars(
                        select(StorageStock).where(
                            StorageStock.warehouse_id == int(default_wh.id),
                            StorageStock.stock != 0,
                        )
                    ).all()
                    for row in rows:
                        sku_s = str(row.sku or "").strip()
                        qty = max(0, int(row.stock or 0))
                        if sku_s and qty:
                            stocks_by_sku[sku_s] = qty

            now = int(time.time())
            legacy_bin_id = int(self._ensure_default_bin(session, legacy_id).id)
            for sku_s, qty in stocks_by_sku.items():
                existing = session.scalar(
                    select(StorageStock).where(
                        StorageStock.warehouse_id == legacy_id,
                        StorageStock.bin_id == legacy_bin_id,
                        StorageStock.sku == sku_s,
                    )
                )
                if existing is None:
                    session.add(
                        StorageStock(
                            warehouse_id=legacy_id,
                            bin_id=legacy_bin_id,
                            sku=sku_s,
                            stock=qty,
                        )
                    )
                else:
                    existing.stock = qty

            default_wh = session.scalar(
                select(StorageWarehouse).where(StorageWarehouse.is_default.is_(True)).limit(1)
            )
            if default_wh is not None and int(default_wh.id) != legacy_id and stocks_by_sku:
                for sku_s in stocks_by_sku:
                    row = session.scalar(
                        select(StorageStock).where(
                            StorageStock.warehouse_id == int(default_wh.id),
                            StorageStock.sku == sku_s,
                        )
                    )
                    if row is not None:
                        session.delete(row)

            legacy_wh.updated_at_ts = now
            self._mark_legacy_migration_done(session)
            session.commit()

    def get_meta(self) -> dict[str, list[dict[str, Any]]]:
        with Session(self.engine) as session:
            groups = session.scalars(
                select(StorageWarehouseGroup).order_by(
                    StorageWarehouseGroup.sort_order, StorageWarehouseGroup.name
                )
            ).all()
        return {"groups": [self._group_dict(g) for g in groups]}

    def _group_dict(self, row: StorageWarehouseGroup) -> dict[str, Any]:
        return {"id": row.id, "name": row.name, "sort_order": row.sort_order}

    def save_groups(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {g.id: g for g in session.scalars(select(StorageWarehouseGroup)).all()}
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                raw_id = item.get("id")
                row = None
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = StorageWarehouseGroup(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for gid, row in existing.items():
                if gid not in keep_ids:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(StorageWarehouseGroup).order_by(
                    StorageWarehouseGroup.sort_order, StorageWarehouseGroup.name
                )
            ).all()
            return [self._group_dict(r) for r in rows]

    def list_warehouses(self, filters: dict[str, str]) -> list[StorageWarehouseRow]:
        with Session(self.engine) as session:
            q = select(StorageWarehouse).order_by(
                StorageWarehouse.is_default.desc(),
                StorageWarehouse.name,
            )
            conds = self._filter_conditions(filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            stats = self._stock_stats(session)
            return [self._warehouse_row(session, r, stats.get(int(r.id), (0, 0))) for r in rows]

    def _stock_stats(self, session: Session) -> dict[int, tuple[int, int]]:
        agg = session.execute(
            select(
                StorageStock.warehouse_id,
                func.count(func.distinct(StorageStock.sku)),
                func.coalesce(func.sum(StorageStock.stock), 0),
            ).group_by(StorageStock.warehouse_id)
        ).all()
        out: dict[int, tuple[int, int]] = {}
        for wh_id, sku_count, total in agg:
            out[int(wh_id)] = (int(sku_count or 0), int(total or 0))
        return out

    def _filter_conditions(self, filters: dict[str, str]) -> list:
        conds = []
        mapping = {
            "name": StorageWarehouse.name,
            "address": StorageWarehouse.address,
            "address_comment": StorageWarehouse.address_comment,
            "comment": StorageWarehouse.comment,
            "code": StorageWarehouse.code,
        }
        for key, col in mapping.items():
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        raw_group = (filters.get("group_id") or "").strip()
        if raw_group:
            try:
                conds.append(StorageWarehouse.group_id == int(raw_group))
            except ValueError:
                pass
        q_text = (filters.get("q") or "").strip()
        if q_text:
            pat = _like(q_text)
            conds.append(
                or_(
                    StorageWarehouse.name.ilike(pat),
                    StorageWarehouse.code.ilike(pat),
                    StorageWarehouse.address.ilike(pat),
                    StorageWarehouse.comment.ilike(pat),
                )
            )
        return conds

    def get_warehouse(self, warehouse_id: int) -> StorageWarehouseRow | None:
        with Session(self.engine) as session:
            row = session.get(StorageWarehouse, int(warehouse_id))
            if row is None:
                return None
            stats = self._stock_stats(session)
            return self._warehouse_row(session, row, stats.get(int(row.id), (0, 0)))

    def create_warehouse(self, data: dict[str, Any]) -> StorageWarehouseRow:
        name = str(data.get("name") or "").strip()
        code = str(data.get("code") or "").strip().upper()
        if not name:
            raise ValueError("Наименование обязательно")
        if not code:
            raise ValueError("Код обязателен")
        if len(code) > 64:
            raise ValueError("Код склада — до 64 символов")
        now = int(time.time())
        with Session(self.engine) as session:
            if session.scalar(select(StorageWarehouse.id).where(StorageWarehouse.code == code)):
                raise ValueError(f"Код «{code}» уже занят")
            row = StorageWarehouse(
                name=name[:256],
                address=str(data.get("address") or "").strip()[:512],
                address_comment=str(data.get("address_comment") or "").strip()[:512],
                comment=str(data.get("comment") or "").strip()[:1024],
                code=code,
                group_id=_opt_int(data.get("group_id")),
                is_default=False,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.flush()
            self._ensure_default_bin(session, int(row.id))
            session.commit()
            session.refresh(row)
            return self._warehouse_row(session, row, (0, 0))

    def update_warehouse(self, warehouse_id: int, data: dict[str, Any]) -> StorageWarehouseRow | None:
        with Session(self.engine) as session:
            row = session.get(StorageWarehouse, int(warehouse_id))
            if row is None:
                return None
            name = str(data.get("name") or "").strip()
            code = str(data.get("code") or "").strip().upper()
            if not name:
                raise ValueError("Наименование обязательно")
            if not code:
                raise ValueError("Код обязателен")
            other = session.scalar(
                select(StorageWarehouse.id).where(
                    StorageWarehouse.code == code,
                    StorageWarehouse.id != row.id,
                )
            )
            if other is not None:
                raise ValueError(f"Код «{code}» уже занят")
            row.name = name[:256]
            row.address = str(data.get("address") or "").strip()[:512]
            row.address_comment = str(data.get("address_comment") or "").strip()[:512]
            row.comment = str(data.get("comment") or "").strip()[:1024]
            row.code = code
            row.group_id = _opt_int(data.get("group_id"))
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            stats = self._stock_stats(session)
            return self._warehouse_row(session, row, stats.get(int(row.id), (0, 0)))

    def _resolve_bin_id(self, session: Session, warehouse_id: int, bin_id: int | None) -> int:
        if bin_id is not None:
            row = session.get(StorageBin, int(bin_id))
            if row is None or int(row.warehouse_id) != int(warehouse_id):
                raise ValueError("Ячейка не найдена на этом складе")
            return int(row.id)
        return int(self._ensure_default_bin(session, int(warehouse_id)).id)

    def get_stock(self, warehouse_id: int, sku: str, *, bin_id: int | None = None) -> int:
        sku_n = sku.strip()
        if not sku_n:
            return 0
        with Session(self.engine) as session:
            if bin_id is None:
                total = session.scalar(
                    select(func.coalesce(func.sum(StorageStock.stock), 0)).where(
                        StorageStock.warehouse_id == int(warehouse_id),
                        StorageStock.sku == sku_n,
                    )
                )
                return int(total or 0)
            row = session.scalar(
                select(StorageStock).where(
                    StorageStock.warehouse_id == int(warehouse_id),
                    StorageStock.bin_id == int(bin_id),
                    StorageStock.sku == sku_n,
                )
            )
            return int(row.stock) if row else 0

    def set_stock(
        self,
        warehouse_id: int,
        sku: str,
        stock: int,
        *,
        skip_recalc: bool = False,
        bin_id: int | None = None,
        strict: bool = False,
    ) -> None:
        sku_n = sku.strip()
        if not sku_n:
            raise ValueError("SKU обязателен")
        qty = int(stock)
        if qty < 0:
            if strict:
                raise InsufficientBinStockError(
                    f"В ячейке недостаточно «{sku_n}»: нужно списать больше, чем есть"
                )
            qty = 0
        with Session(self.engine) as session:
            resolved_bin = self._resolve_bin_id(session, int(warehouse_id), bin_id)
            row = session.scalar(
                select(StorageStock).where(
                    StorageStock.warehouse_id == int(warehouse_id),
                    StorageStock.bin_id == resolved_bin,
                    StorageStock.sku == sku_n,
                )
            )
            if row is None:
                if qty == 0:
                    session.commit()
                    if not skip_recalc and self._on_sku_changed:
                        self._on_sku_changed({sku_n})
                    return
                session.add(
                    StorageStock(
                        warehouse_id=int(warehouse_id),
                        bin_id=resolved_bin,
                        sku=sku_n,
                        stock=qty,
                    )
                )
            else:
                if qty == 0:
                    session.delete(row)
                else:
                    row.stock = qty
            session.commit()
        if not skip_recalc and self._on_sku_changed:
            self._on_sku_changed({sku_n})

    def adjust_stock(
        self,
        warehouse_id: int,
        sku: str,
        delta: int,
        *,
        skip_recalc: bool = False,
        bin_id: int | None = None,
        strict: bool = False,
    ) -> None:
        if not int(delta):
            return
        current = self.get_stock(int(warehouse_id), sku, bin_id=bin_id)
        if bin_id is None:
            with Session(self.engine) as session:
                bin_id = self._resolve_bin_id(session, int(warehouse_id), None)
                session.commit()
            current = self.get_stock(int(warehouse_id), sku, bin_id=bin_id)
        self.set_stock(
            int(warehouse_id),
            sku,
            current + int(delta),
            skip_recalc=skip_recalc,
            bin_id=bin_id,
            strict=strict,
        )

    def adjust_stocks(
        self,
        warehouse_id: int,
        deltas_by_sku: dict[str, int],
        *,
        skip_recalc: bool = False,
        bin_id: int | None = None,
        strict: bool = False,
    ) -> None:
        if not deltas_by_sku:
            return
        changed: set[str] = set()
        for sku, delta in deltas_by_sku.items():
            sku_n = str(sku or "").strip()
            if not sku_n or not int(delta):
                continue
            self.adjust_stock(
                int(warehouse_id),
                sku_n,
                int(delta),
                skip_recalc=True,
                bin_id=bin_id,
                strict=strict,
            )
            changed.add(sku_n)
        if not skip_recalc and self._on_sku_changed and changed:
            self._on_sku_changed(changed)

    def transfer_between_bins(
        self,
        warehouse_id: int,
        sku: str,
        qty: int,
        *,
        from_bin_id: int,
        to_bin_id: int,
    ) -> None:
        sku_n = str(sku or "").strip()
        amount = int(qty)
        if not sku_n:
            raise ValueError("SKU обязателен")
        if amount <= 0:
            raise ValueError("Количество должно быть больше нуля")
        if int(from_bin_id) == int(to_bin_id):
            raise ValueError("Ячейки откуда и куда должны отличаться")
        self.adjust_stock(
            int(warehouse_id),
            sku_n,
            -amount,
            skip_recalc=True,
            bin_id=int(from_bin_id),
            strict=True,
        )
        try:
            self.adjust_stock(
                int(warehouse_id),
                sku_n,
                amount,
                skip_recalc=True,
                bin_id=int(to_bin_id),
            )
        except Exception:
            self.adjust_stock(
                int(warehouse_id),
                sku_n,
                amount,
                skip_recalc=True,
                bin_id=int(from_bin_id),
            )
            raise
        if self._on_sku_changed:
            self._on_sku_changed({sku_n})

    def list_bins(self, warehouse_id: int) -> list[StorageBinRow]:
        with Session(self.engine) as session:
            self._ensure_default_bin(session, int(warehouse_id))
            session.commit()
            bins = session.scalars(
                select(StorageBin)
                .where(StorageBin.warehouse_id == int(warehouse_id))
                .order_by(StorageBin.is_default.desc(), StorageBin.code)
            ).all()
            stats = session.execute(
                select(
                    StorageStock.bin_id,
                    func.count(func.distinct(StorageStock.sku)),
                    func.coalesce(func.sum(StorageStock.stock), 0),
                )
                .where(StorageStock.warehouse_id == int(warehouse_id))
                .group_by(StorageStock.bin_id)
            ).all()
            by_bin = {int(b): (int(c or 0), int(t or 0)) for b, c, t in stats}
            return [self._bin_row(b, by_bin.get(int(b.id), (0, 0))) for b in bins]

    def create_bin(self, warehouse_id: int, data: dict[str, Any]) -> StorageBinRow:
        code = str(data.get("code") or "").strip().upper()
        name = str(data.get("name") or "").strip() or code
        if not code:
            raise ValueError("Код ячейки обязателен")
        now = int(time.time())
        with Session(self.engine) as session:
            if session.get(StorageWarehouse, int(warehouse_id)) is None:
                raise ValueError("Склад не найден")
            self._ensure_default_bin(session, int(warehouse_id))
            if session.scalar(
                select(StorageBin.id).where(
                    StorageBin.warehouse_id == int(warehouse_id),
                    StorageBin.code == code,
                )
            ):
                raise ValueError(f"Ячейка «{code}» уже есть на складе")
            row = StorageBin(
                warehouse_id=int(warehouse_id),
                code=code[:64],
                name=name[:256],
                is_default=False,
                created_at_ts=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._bin_row(row, (0, 0))

    def update_bin(self, warehouse_id: int, bin_id: int, data: dict[str, Any]) -> StorageBinRow | None:
        with Session(self.engine) as session:
            row = session.get(StorageBin, int(bin_id))
            if row is None or int(row.warehouse_id) != int(warehouse_id):
                return None
            code = str(data.get("code") or row.code).strip().upper()
            name = str(data.get("name") or row.name).strip()
            if not code:
                raise ValueError("Код ячейки обязателен")
            other = session.scalar(
                select(StorageBin.id).where(
                    StorageBin.warehouse_id == int(warehouse_id),
                    StorageBin.code == code,
                    StorageBin.id != row.id,
                )
            )
            if other is not None:
                raise ValueError(f"Ячейка «{code}» уже есть на складе")
            if row.is_default and code != _DEFAULT_BIN_CODE:
                raise ValueError("Код основной ячейки менять нельзя")
            row.code = code[:64]
            row.name = (name or code)[:256]
            session.commit()
            session.refresh(row)
            stats = session.execute(
                select(
                    func.count(func.distinct(StorageStock.sku)),
                    func.coalesce(func.sum(StorageStock.stock), 0),
                ).where(StorageStock.bin_id == int(row.id))
            ).one()
            return self._bin_row(row, (int(stats[0] or 0), int(stats[1] or 0)))

    def delete_bin(self, warehouse_id: int, bin_id: int) -> None:
        with Session(self.engine) as session:
            row = session.get(StorageBin, int(bin_id))
            if row is None or int(row.warehouse_id) != int(warehouse_id):
                raise ValueError("Ячейка не найдена")
            if row.is_default or row.code == _DEFAULT_BIN_CODE:
                raise ValueError("Основную ячейку удалить нельзя")
            qty = int(
                session.scalar(
                    select(func.coalesce(func.sum(StorageStock.stock), 0)).where(
                        StorageStock.bin_id == int(row.id)
                    )
                )
                or 0
            )
            if qty > 0:
                raise ValueError("Нельзя удалить ячейку с остатком")
            session.execute(delete(StorageStock).where(StorageStock.bin_id == int(row.id)))
            session.delete(row)
            session.commit()

    def _bin_row(self, row: StorageBin, stats: tuple[int, int]) -> StorageBinRow:
        sku_count, total_stock = stats
        return StorageBinRow(
            id=int(row.id),
            warehouse_id=int(row.warehouse_id),
            code=row.code,
            name=row.name or row.code,
            is_default=bool(row.is_default),
            sku_count=sku_count,
            total_stock=total_stock,
            created_at_ts=int(row.created_at_ts),
        )

    def bin_to_dict(self, row: StorageBinRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "warehouse_id": row.warehouse_id,
            "code": row.code,
            "name": row.name,
            "is_default": row.is_default,
            "sku_count": row.sku_count,
            "total_stock": row.total_stock,
            "created_at_ts": row.created_at_ts,
        }

    def warehouses_with_bins(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for wh in self.list_warehouses({}):
            item = self.warehouse_to_dict(wh)
            item["bins"] = [self.bin_to_dict(b) for b in self.list_bins(int(wh.id))]
            out.append(item)
        return out

    def clear_stocks_for_warehouse(self, warehouse_id: int) -> dict[str, Any]:
        """Обнуляет все остатки на складе (удаляет записи storage_stocks)."""
        wid = int(warehouse_id)
        with Session(self.engine) as session:
            rows = session.scalars(
                select(StorageStock).where(StorageStock.warehouse_id == wid)
            ).all()
            skus = sorted({str(r.sku).strip() for r in rows if str(r.sku or "").strip()})
            cleared_units = sum(max(0, int(r.stock)) for r in rows)
            session.execute(delete(StorageStock).where(StorageStock.warehouse_id == wid))
            session.commit()
        if skus and self._on_sku_changed:
            self._on_sku_changed(set(skus))
        return {
            "cleared_skus": len(skus),
            "cleared_units": cleared_units,
            "skus": skus,
        }

    def list_stocks_for_warehouse(self, warehouse_id: int) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(StorageStock.sku, func.sum(StorageStock.stock))
                .where(StorageStock.warehouse_id == int(warehouse_id))
                .group_by(StorageStock.sku)
            ).all()
            return {str(sku): int(total or 0) for sku, total in rows if int(total or 0) > 0}

    def list_stocks_for_bin(self, warehouse_id: int, bin_id: int) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(StorageStock).where(
                    StorageStock.warehouse_id == int(warehouse_id),
                    StorageStock.bin_id == int(bin_id),
                )
            ).all()
            return {r.sku: int(r.stock) for r in rows if int(r.stock) > 0}

    def total_stock_by_sku(self) -> dict[str, int]:
        """Суммарный остаток SKU по всем складам (для будущей синхронизации)."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(StorageStock.sku, func.sum(StorageStock.stock)).group_by(StorageStock.sku)
            ).all()
            return {str(sku): int(total or 0) for sku, total in rows if int(total or 0) > 0}

    def _warehouse_row(
        self,
        session: Session,
        row: StorageWarehouse,
        stats: tuple[int, int],
    ) -> StorageWarehouseRow:
        group_name = ""
        if row.group_id:
            gr = session.get(StorageWarehouseGroup, row.group_id)
            if gr:
                group_name = gr.name
        sku_count, total_stock = stats
        return StorageWarehouseRow(
            id=int(row.id),
            name=row.name,
            address=row.address or "",
            address_comment=row.address_comment or "",
            comment=row.comment or "",
            code=row.code,
            group_id=row.group_id,
            group_name=group_name,
            is_default=bool(row.is_default),
            sku_count=sku_count,
            total_stock=total_stock,
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
        )

    def warehouse_to_dict(self, row: StorageWarehouseRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "address": row.address,
            "address_comment": row.address_comment,
            "comment": row.comment,
            "code": row.code,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "is_default": row.is_default,
            "sku_count": row.sku_count,
            "total_stock": row.total_stock,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }


def _opt_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ensure_integer_column(engine, table: str, column: str) -> None:
    """Добавляет INTEGER-колонку, если её ещё нет (SQLite / Postgres)."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column in cols:
        return
    with Session(engine) as session:
        session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER"))
        session.commit()
