"""Склады хранения (единицы учёта остатков в новой панели)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, func, inspect, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

_DEFAULT_WAREHOUSE_NAME = "Основной склад"
_DEFAULT_WAREHOUSE_CODE = "MAIN"
_LEGACY_WAREHOUSE_NAME = "Старый склад"
_LEGACY_WAREHOUSE_CODE = "LEGACY"
_LEGACY_STOCK_MIGRATION_KEY = "legacy_warehouse_stock_v1"


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


class StorageStock(_Base):
    """Остатки SKU на конкретном складе (новая панель)."""

    __tablename__ = "storage_stocks"
    __table_args__ = (UniqueConstraint("warehouse_id", "sku", name="uq_storage_stock_wh_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storage_warehouses.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)
        self._on_sku_changed: Callable[[str], None] | None = None

    def set_stock_balance_hook(self, on_sku_changed: Callable[[str], None] | None) -> None:
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
            session.commit()

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
            for sku_s, qty in stocks_by_sku.items():
                existing = session.scalar(
                    select(StorageStock).where(
                        StorageStock.warehouse_id == legacy_id,
                        StorageStock.sku == sku_s,
                    )
                )
                if existing is None:
                    session.add(StorageStock(warehouse_id=legacy_id, sku=sku_s, stock=qty))
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
                func.count(StorageStock.sku),
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

    def get_stock(self, warehouse_id: int, sku: str) -> int:
        sku_n = sku.strip()
        if not sku_n:
            return 0
        with Session(self.engine) as session:
            row = session.scalar(
                select(StorageStock).where(
                    StorageStock.warehouse_id == int(warehouse_id),
                    StorageStock.sku == sku_n,
                )
            )
            return int(row.stock) if row else 0

    def set_stock(self, warehouse_id: int, sku: str, stock: int, *, skip_recalc: bool = False) -> None:
        sku_n = sku.strip()
        if not sku_n:
            raise ValueError("SKU обязателен")
        qty = max(0, int(stock))
        with Session(self.engine) as session:
            row = session.scalar(
                select(StorageStock).where(
                    StorageStock.warehouse_id == int(warehouse_id),
                    StorageStock.sku == sku_n,
                )
            )
            if row is None:
                if qty == 0:
                    if not skip_recalc and self._on_sku_changed:
                        self._on_sku_changed(sku_n)
                    return
                session.add(StorageStock(warehouse_id=int(warehouse_id), sku=sku_n, stock=qty))
            else:
                if qty == 0:
                    session.delete(row)
                else:
                    row.stock = qty
            session.commit()
        if not skip_recalc and self._on_sku_changed:
            self._on_sku_changed(sku_n)

    def adjust_stock(
        self, warehouse_id: int, sku: str, delta: int, *, skip_recalc: bool = False
    ) -> None:
        if not int(delta):
            return
        current = self.get_stock(int(warehouse_id), sku)
        self.set_stock(int(warehouse_id), sku, current + int(delta), skip_recalc=skip_recalc)

    def adjust_stocks(
        self, warehouse_id: int, deltas_by_sku: dict[str, int], *, skip_recalc: bool = False
    ) -> None:
        if not deltas_by_sku:
            return
        for sku, delta in deltas_by_sku.items():
            sku_n = str(sku or "").strip()
            if not sku_n or not int(delta):
                continue
            self.adjust_stock(int(warehouse_id), sku_n, int(delta), skip_recalc=True)
        if not skip_recalc and self._on_sku_changed:
            for sku in deltas_by_sku:
                sku_n = str(sku or "").strip()
                if sku_n:
                    self._on_sku_changed(sku_n)

    def list_stocks_for_warehouse(self, warehouse_id: int) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(StorageStock).where(StorageStock.warehouse_id == int(warehouse_id))
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
