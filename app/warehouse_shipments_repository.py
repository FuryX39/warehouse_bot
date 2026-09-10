"""Документ отгрузки складских заказов покупателей."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.repositories import InventoryRepository, OrderItem
from app.ship_movement import record_fbs_ship_movement
from app.storage_warehouse_repository import InsufficientBinStockError, StorageWarehouseRepository
from app.warehouse_orders_repository import (
    ORDER_CANCELLED,
    ORDER_SHIPPED,
    WarehouseOrder,
    WarehouseOrderLine,
    WarehouseOrdersRepository,
)

SHIP_DRAFT = "draft"
SHIP_POSTED = "posted"


class _Base(DeclarativeBase):
    pass


class WarehouseShipment(_Base):
    __tablename__ = "warehouse_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SHIP_DRAFT)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posted_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseShipmentOrder(_Base):
    __tablename__ = "warehouse_shipment_orders"
    __table_args__ = (UniqueConstraint("shipment_id", "order_id", name="uq_warehouse_shipment_order_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_shipments.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)


class WarehouseShipmentLine(_Base):
    __tablename__ = "warehouse_shipment_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_shipments.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class ShipmentRow:
    id: int
    number: str
    title: str
    status: str
    origin: str
    created_at_ts: int
    posted_at_ts: int
    order_ids: list[int] = field(default_factory=list)
    posting_ids: list[str] = field(default_factory=list)
    lines: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class WarehouseShipmentsRepository:
    def __init__(
        self,
        db_url: str,
        orders_repo: WarehouseOrdersRepository,
        storage_repo: StorageWarehouseRepository,
        inventory_repo: InventoryRepository,
        movement_repo=None,
    ) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.orders_repo = orders_repo
        self.storage_repo = storage_repo
        self.inventory_repo = inventory_repo
        self.movement_repo = movement_repo

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def _next_number(self, session: Session) -> str:
        max_n = 0
        for num in session.scalars(select(WarehouseShipment.number)).all():
            text = str(num or "")
            if text.startswith("ОТ-") and text[3:].isdigit():
                max_n = max(max_n, int(text[3:]))
        return f"ОТ-{max_n + 1:05d}"

    def list_shipments(self) -> list[ShipmentRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseShipment).order_by(
                    WarehouseShipment.created_at_ts.desc(), WarehouseShipment.id.desc()
                )
            ).all()
            return [self._row(session, r) for r in rows]

    def get_shipment(self, shipment_id: int) -> ShipmentRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseShipment, int(shipment_id))
            if row is None:
                return None
            return self._row(session, row)

    def create_draft(
        self,
        order_ids: list[int],
        *,
        title: str = "",
        origin: str = "manual",
        post: bool = False,
    ) -> ShipmentRow:
        ids = sorted({int(i) for i in order_ids if int(i) > 0})
        if not ids:
            raise ValueError("Выберите заказы")
        now = int(time.time())
        with Session(self.engine) as session:
            skip: list[int] = []
            take: list[int] = []
            for oid in ids:
                order = session.get(WarehouseOrder, oid)
                if order is None or order.status in (ORDER_SHIPPED, ORDER_CANCELLED):
                    skip.append(oid)
                    continue
                take.append(oid)
            if not take:
                raise ValueError("Нет заказов для отгрузки (уже отгружены или в другом документе)")
            ship = WarehouseShipment(
                number=self._next_number(session),
                title=(title or "Отгрузка")[:256],
                status=SHIP_DRAFT,
                origin=str(origin or "manual")[:16],
                created_at_ts=now,
            )
            session.add(ship)
            session.flush()
            qty_by_sku: dict[str, tuple[str, int]] = {}
            for oid in take:
                session.add(WarehouseShipmentOrder(shipment_id=int(ship.id), order_id=oid))
                for line in session.scalars(
                    select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == oid)
                ).all():
                    sku = str(line.sku)
                    name, qty = qty_by_sku.get(sku, (str(line.name), 0))
                    qty_by_sku[sku] = (name or str(line.name), qty + int(line.quantity))
            for sku, (name, qty) in qty_by_sku.items():
                session.add(
                    WarehouseShipmentLine(
                        shipment_id=int(ship.id), sku=sku, name=name[:512], quantity=qty
                    )
                )
            session.commit()
            session.refresh(ship)
            row = self._row(session, ship)
        if skip:
            row.warnings.append(f"Пропущены заказы: {skip}")
        if post:
            return self.post_shipment(int(row.id))
        return row

    def post_shipment(self, shipment_id: int) -> ShipmentRow:
        return self._post_shipment_checked(int(shipment_id))

    def _post_shipment_checked(self, shipment_id: int) -> ShipmentRow:
        warnings: list[str] = []
        with Session(self.engine) as session:
            ship = session.get(WarehouseShipment, int(shipment_id))
            if ship is None:
                raise ValueError("Отгрузка не найдена")
            if ship.status == SHIP_POSTED:
                return self._row(session, ship)
            links = session.scalars(
                select(WarehouseShipmentOrder).where(
                    WarehouseShipmentOrder.shipment_id == int(ship.id)
                )
            ).all()
            posted_ids: list[int] = []
            qty_by_sku: dict[str, int] = {}
            external_ids: list[str] = []
            source_for_journal = ""
            for lk in links:
                order = session.get(WarehouseOrder, int(lk.order_id))
                if order is None or order.status in (ORDER_SHIPPED, ORDER_CANCELLED):
                    continue
                order_lines = list(
                    session.scalars(
                        select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order.id))
                    ).all()
                )
                ok = True
                for ln in order_lines:
                    if int(ln.quantity) <= 0:
                        continue
                    have = self.storage_repo.get_stock(int(order.warehouse_id), str(ln.sku))
                    # MAIN specifically
                    bin_id = self.storage_repo.get_default_bin_id(int(order.warehouse_id))
                    have = self.storage_repo.get_stock(
                        int(order.warehouse_id), str(ln.sku), bin_id=bin_id
                    )
                    if have < int(ln.quantity):
                        ok = False
                        break
                if not ok:
                    warnings.append(f"Нехватка MAIN для {order.posting_id}")
                    continue
                for ln in order_lines:
                    if int(ln.quantity) <= 0:
                        continue
                    bin_id = self.storage_repo.get_default_bin_id(int(order.warehouse_id))
                    self.storage_repo.adjust_stock(
                        int(order.warehouse_id),
                        str(ln.sku),
                        -int(ln.quantity),
                        skip_recalc=True,
                        bin_id=bin_id,
                        strict=True,
                    )
                    qty_by_sku[str(ln.sku)] = qty_by_sku.get(str(ln.sku), 0) + int(ln.quantity)
                    external_ids.append(f"{order.posting_id}:{ln.sku}")
                order.status = ORDER_SHIPPED
                order.updated_at_ts = int(time.time())
                posted_ids.append(int(order.id))
                source_for_journal = order.source
            if not posted_ids:
                session.commit()
                row = self._row(session, ship)
                row.warnings.extend(warnings)
                return row
            for lk in list(links):
                order = session.get(WarehouseOrder, int(lk.order_id))
                if order is None or order.status != ORDER_SHIPPED:
                    session.delete(lk)
            ship.status = SHIP_POSTED
            ship.posted_at_ts = int(time.time())
            session.commit()
        if posted_ids:
            self._mark_order_items_shipped(external_ids)
            if self.movement_repo is not None and qty_by_sku:
                record_fbs_ship_movement(
                    self.movement_repo,
                    source=source_for_journal or "warehouse",
                    external_order_ids=external_ids,
                    qty_by_sku=qty_by_sku,
                    journal_source="warehouse",
                )
        with Session(self.engine) as session:
            ship = session.get(WarehouseShipment, int(shipment_id))
            row = self._row(session, ship)
            row.warnings.extend(warnings)
            return row

    def _mark_order_items_shipped(self, external_ids: list[str]) -> None:
        if not external_ids:
            return
        by_source: dict[str, set[str]] = {}
        with Session(self.engine) as session:
            for ext in external_ids:
                posting = str(ext).split(":", 1)[0]
                rows = session.scalars(
                    select(OrderItem).where(OrderItem.external_order_id.like(f"{posting}:%"))
                ).all()
                for r in rows:
                    if r.state != "shipped":
                        r.state = "shipped"
            session.commit()
        _ = by_source

    def ship_postings(
        self,
        source: str,
        posting_ids: list[str],
        *,
        title: str,
        origin: str = "sync",
    ) -> ShipmentRow | None:
        order_ids: list[int] = []
        for pid in posting_ids:
            row = self.orders_repo.get_by_posting(source, pid)
            if row and row.status not in (ORDER_SHIPPED, ORDER_CANCELLED):
                order_ids.append(int(row.id))
        if not order_ids:
            return None
        draft = self.create_draft(order_ids, title=title, origin=origin, post=False)
        return self.post_shipment(int(draft.id))

    def create_from_packing_job(
        self,
        job_id: int,
        *,
        title: str = "",
        post: bool = True,
    ) -> ShipmentRow:
        orders = self.orders_repo.list_by_packing_job(int(job_id))
        ids = [int(o.id) for o in orders if o.status not in (ORDER_SHIPPED, ORDER_CANCELLED)]
        if not ids:
            raise ValueError("В волне нет заказов для отгрузки")
        return self.create_draft(
            ids,
            title=title or f"Отгрузка волны {int(job_id)}",
            origin="wave",
            post=post,
        )

    def to_dict(self, row: ShipmentRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "number": row.number,
            "title": row.title,
            "status": row.status,
            "origin": row.origin,
            "created_at_ts": row.created_at_ts,
            "posted_at_ts": row.posted_at_ts,
            "order_ids": row.order_ids,
            "posting_ids": row.posting_ids,
            "lines": row.lines,
            "warnings": row.warnings,
        }

    def _row(self, session: Session, row: WarehouseShipment) -> ShipmentRow:
        links = session.scalars(
            select(WarehouseShipmentOrder).where(WarehouseShipmentOrder.shipment_id == int(row.id))
        ).all()
        order_ids = [int(lk.order_id) for lk in links]
        posting_ids: list[str] = []
        for oid in order_ids:
            order = session.get(WarehouseOrder, oid)
            if order:
                posting_ids.append(str(order.posting_id))
        lines = [
            {"sku": str(ln.sku), "name": str(ln.name), "quantity": int(ln.quantity)}
            for ln in session.scalars(
                select(WarehouseShipmentLine).where(WarehouseShipmentLine.shipment_id == int(row.id))
            ).all()
        ]
        return ShipmentRow(
            id=int(row.id),
            number=str(row.number),
            title=str(row.title),
            status=str(row.status),
            origin=str(row.origin),
            created_at_ts=int(row.created_at_ts),
            posted_at_ts=int(row.posted_at_ts),
            order_ids=order_ids,
            posting_ids=posting_ids,
            lines=lines,
        )
