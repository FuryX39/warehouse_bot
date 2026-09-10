"""Перемещения между ячейками одного склада."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogProduct
from app.storage_warehouse_repository import (
    StorageBin,
    StorageWarehouse,
    StorageWarehouseRepository,
)


class _Base(DeclarativeBase):
    pass


class WarehouseBinTransfer(_Base):
    __tablename__ = "warehouse_bin_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_bin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_bin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseBinTransferItem(_Base):
    __tablename__ = "warehouse_bin_transfer_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_bin_transfers.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class BinTransferItemRow:
    id: Optional[int]
    product_id: int
    sku: str
    name: str
    quantity: int
    sort_order: int = 0


@dataclass
class BinTransferRow:
    id: int
    title: str
    warehouse_id: int
    warehouse_name: str
    from_bin_id: int
    from_bin_name: str
    to_bin_id: int
    to_bin_name: str
    comment: str
    total_quantity: int
    created_at_ts: int
    items: list[BinTransferItemRow] = field(default_factory=list)


class WarehouseBinTransfersRepository:
    def __init__(self, db_url: str, storage_repo: StorageWarehouseRepository) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.storage_repo = storage_repo

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def list_transfers(self) -> list[BinTransferRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseBinTransfer).order_by(
                    WarehouseBinTransfer.created_at_ts.desc(), WarehouseBinTransfer.id.desc()
                )
            ).all()
            return [self._row(session, r, load_items=False) for r in rows]

    def get_transfer(self, transfer_id: int) -> BinTransferRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseBinTransfer, int(transfer_id))
            if row is None:
                return None
            return self._row(session, row, load_items=True)

    def create_transfer(self, data: dict[str, Any]) -> BinTransferRow:
        title = str(data.get("title") or "").strip() or "Перемещение по ячейкам"
        try:
            warehouse_id = int(data.get("warehouse_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Выберите склад") from exc
        from_bin_id = self.storage_repo.resolve_bin_id(warehouse_id, data.get("from_bin_id"))
        to_bin_id = self.storage_repo.resolve_bin_id(warehouse_id, data.get("to_bin_id"))
        if from_bin_id == to_bin_id:
            raise ValueError("Ячейки откуда и куда должны отличаться")
        items_raw = data.get("items")
        if not isinstance(items_raw, list) or not items_raw:
            raise ValueError("Добавьте хотя бы один товар")
        now = int(time.time())
        comment = str(data.get("comment") or "").strip()[:2048]
        prepared: list[tuple[int, str, str, int, int]] = []
        with Session(self.engine) as session:
            if session.get(StorageWarehouse, warehouse_id) is None:
                raise ValueError("Склад не найден")
            for i, raw in enumerate(items_raw):
                if not isinstance(raw, dict):
                    continue
                try:
                    product_id = int(raw.get("product_id"))
                    qty = int(raw.get("quantity"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Некорректная строка товара") from exc
                if qty <= 0:
                    raise ValueError("Количество должно быть больше нуля")
                product = session.get(CatalogProduct, product_id)
                if product is None:
                    raise ValueError(f"Товар id={product_id} не найден")
                sku = str(product.sku or "").strip()
                prepared.append((product_id, sku, str(product.name)[:512], qty, i))

        if not prepared:
            raise ValueError("Добавьте хотя бы один товар")
        for _pid, sku, _name, qty, _i in prepared:
            self.storage_repo.transfer_between_bins(
                warehouse_id, sku, qty, from_bin_id=from_bin_id, to_bin_id=to_bin_id
            )

        with Session(self.engine) as session:
            transfer = WarehouseBinTransfer(
                title=title[:256],
                warehouse_id=warehouse_id,
                from_bin_id=from_bin_id,
                to_bin_id=to_bin_id,
                comment=comment,
                created_at_ts=now,
            )
            session.add(transfer)
            session.flush()
            total = 0
            for product_id, sku, name, qty, sort_order in prepared:
                session.add(
                    WarehouseBinTransferItem(
                        transfer_id=int(transfer.id),
                        product_id=product_id,
                        sku=sku,
                        name=name,
                        quantity=qty,
                        sort_order=sort_order,
                    )
                )
                total += qty
            transfer.total_quantity = total
            session.commit()
            session.refresh(transfer)
            return self._row(session, transfer, load_items=True)

    def to_dict(self, row: BinTransferRow, *, include_items: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": row.id,
            "title": row.title,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "from_bin_id": row.from_bin_id,
            "from_bin_name": row.from_bin_name,
            "to_bin_id": row.to_bin_id,
            "to_bin_name": row.to_bin_name,
            "comment": row.comment,
            "total_quantity": row.total_quantity,
            "created_at_ts": row.created_at_ts,
        }
        if include_items:
            d["items"] = [
                {
                    "id": i.id,
                    "product_id": i.product_id,
                    "sku": i.sku,
                    "name": i.name,
                    "quantity": i.quantity,
                }
                for i in row.items
            ]
        return d

    def _row(self, session: Session, row: WarehouseBinTransfer, *, load_items: bool) -> BinTransferRow:
        wh = session.get(StorageWarehouse, int(row.warehouse_id))
        fb = session.get(StorageBin, int(row.from_bin_id))
        tb = session.get(StorageBin, int(row.to_bin_id))
        items: list[BinTransferItemRow] = []
        if load_items:
            for it in session.scalars(
                select(WarehouseBinTransferItem)
                .where(WarehouseBinTransferItem.transfer_id == int(row.id))
                .order_by(WarehouseBinTransferItem.sort_order)
            ).all():
                items.append(
                    BinTransferItemRow(
                        id=int(it.id),
                        product_id=int(it.product_id),
                        sku=str(it.sku),
                        name=str(it.name),
                        quantity=int(it.quantity),
                        sort_order=int(it.sort_order),
                    )
                )
        return BinTransferRow(
            id=int(row.id),
            title=str(row.title),
            warehouse_id=int(row.warehouse_id),
            warehouse_name=wh.name if wh else "",
            from_bin_id=int(row.from_bin_id),
            from_bin_name=(fb.name or fb.code) if fb else "",
            to_bin_id=int(row.to_bin_id),
            to_bin_name=(tb.name or tb.code) if tb else "",
            comment=str(row.comment or ""),
            total_quantity=int(row.total_quantity),
            created_at_ts=int(row.created_at_ts),
            items=items,
        )
