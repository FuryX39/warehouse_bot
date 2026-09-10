"""Заказы покупателей: общий документ, не карточка маркетплейса.

Для заказов с МП номер отправления пишется в комментарий, площадка — контрагент CRM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogProduct
from app.repositories import OrderItem


ORDER_OPEN = "open"
ORDER_CANCELLED = "cancelled"
ORDER_IN_WAVE = "in_wave"
ORDER_PACKED = "packed"
ORDER_SHIPPED = "shipped"
RESERVE_STATUSES = (ORDER_OPEN, ORDER_IN_WAVE, ORDER_PACKED)
TERMINAL_STATUSES = (ORDER_CANCELLED, ORDER_SHIPPED)
ORDERS_LIST_PAGE_SIZE = 50

SOURCE_OZON = "ozon"
SOURCE_WB = "wildberries"
SOURCE_YM = "yandex_market"
SOURCE_COUNTERPARTY_NAMES = {
    SOURCE_OZON: "Ozon",
    SOURCE_WB: "Wildberries",
    SOURCE_YM: "Яндекс Маркет",
}


class _Base(DeclarativeBase):
    pass


class WarehouseOrder(_Base):
    __tablename__ = "warehouse_orders"
    __table_args__ = (UniqueConstraint("source", "posting_id", name="uq_warehouse_order_source_posting"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_id: Mapped[str] = mapped_column(String(128), nullable=False)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ORDER_OPEN)
    packing_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseOrderLine(_Base):
    __tablename__ = "warehouse_order_lines"
    __table_args__ = (UniqueConstraint("order_id", "sku", name="uq_warehouse_order_line_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_orders.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class WarehouseOrderLineRow:
    id: int
    sku: str
    product_id: int | None
    name: str
    quantity: int


@dataclass
class WarehouseOrderRow:
    id: int
    number: str
    warehouse_id: int
    source: str
    posting_id: str
    counterparty_id: int | None
    counterparty_name: str
    comment: str
    status: str
    packing_job_id: int | None
    created_at_ts: int
    updated_at_ts: int
    lines: list[WarehouseOrderLineRow] = field(default_factory=list)


def posting_id_from_external(external_order_id: str) -> str:
    raw = str(external_order_id or "").strip()
    if not raw:
        return ""
    return raw.split(":", 1)[0].strip()


def packing_source(marketplace: str) -> str:
    name = str(marketplace or "").strip().lower()
    if name in {"yandex", "yandex_market"}:
        return SOURCE_YM
    if name in {"wb", "wildberries"}:
        return SOURCE_WB
    if name == "ozon":
        return SOURCE_OZON
    return name


class WarehouseOrdersRepository:
    def __init__(self, db_url: str) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self._crm_table_exists: bool | None = None

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._migrate_customer_order_columns()
        self._ensure_marketplace_counterparties()
        self._backfill_comment_and_counterparty()

    def _ensure_marketplace_counterparties(self) -> None:
        with Session(self.engine) as session:
            if not self._crm_ready(session):
                return
            for source in SOURCE_COUNTERPARTY_NAMES:
                self._ensure_counterparty_id(session, source)
            session.commit()

    def _migrate_customer_order_columns(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_orders" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_orders")}
        statements: list[str] = []
        if "comment" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN comment VARCHAR(1024) NOT NULL DEFAULT ''"
            )
        if "counterparty_id" not in cols:
            statements.append("ALTER TABLE warehouse_orders ADD COLUMN counterparty_id INTEGER")
        if not statements:
            return
        with Session(self.engine) as session:
            for sql in statements:
                session.execute(text(sql))
            session.commit()

    def _crm_ready(self, session: Session) -> bool:
        if self._crm_table_exists is None:
            from sqlalchemy import text

            self._crm_table_exists = (
                session.execute(text("SELECT to_regclass('crm_counterparties')")).scalar() is not None
            )
        return self._crm_table_exists

    def _ensure_counterparty_id(self, session: Session, source: str) -> int | None:
        name = SOURCE_COUNTERPARTY_NAMES.get(str(source or "").strip())
        if not name or not self._crm_ready(session):
            return None
        from app.crm_repository import CrmCounterparty

        row = session.scalar(select(CrmCounterparty).where(CrmCounterparty.full_name == name))
        if row is None:
            now = int(time.time())
            row = CrmCounterparty(full_name=name, created_at_ts=now, updated_at_ts=now)
            session.add(row)
            session.flush()
        return int(row.id)

    def _counterparty_name(self, session: Session, row: WarehouseOrder) -> str:
        if row.counterparty_id and self._crm_ready(session):
            from app.crm_repository import CrmCounterparty

            cp = session.get(CrmCounterparty, int(row.counterparty_id))
            if cp and str(cp.full_name or "").strip():
                return str(cp.full_name)
        return SOURCE_COUNTERPARTY_NAMES.get(str(row.source or ""), "")

    def _backfill_comment_and_counterparty(self) -> None:
        from sqlalchemy import inspect

        if "warehouse_orders" not in inspect(self.engine).get_table_names():
            return
        with Session(self.engine) as session:
            rows = session.scalars(select(WarehouseOrder)).all()
            changed = False
            for row in rows:
                if not str(row.comment or "").strip() and str(row.posting_id or "").strip():
                    row.comment = str(row.posting_id)
                    changed = True
                if not row.counterparty_id:
                    cid = self._ensure_counterparty_id(session, str(row.source))
                    if cid:
                        row.counterparty_id = cid
                        changed = True
            if changed:
                session.commit()

    def next_number(self, session: Session) -> str:
        rows = session.scalars(select(WarehouseOrder.number)).all()
        max_n = 0
        for num in rows:
            text = str(num or "")
            if text.startswith("ЗК-") and text[3:].isdigit():
                max_n = max(max_n, int(text[3:]))
        return f"ЗК-{max_n + 1:05d}"

    def upsert_from_posting(
        self,
        *,
        source: str,
        posting_id: str,
        warehouse_id: int,
        lines: list[dict[str, Any]],
        now_ts: int | None = None,
    ) -> WarehouseOrderRow:
        src = str(source or "").strip()
        pid = str(posting_id or "").strip()
        if not src or not pid:
            raise ValueError("source и posting_id обязательны")
        now = int(now_ts or time.time())
        with Session(self.engine) as session:
            row = session.scalar(
                select(WarehouseOrder).where(
                    WarehouseOrder.source == src,
                    WarehouseOrder.posting_id == pid,
                )
            )
            catalog = {
                str(p.sku or "").strip().casefold(): p
                for p in session.scalars(select(CatalogProduct)).all()
                if str(p.sku or "").strip()
            }
            if row is None:
                row = WarehouseOrder(
                    number=self.next_number(session),
                    warehouse_id=int(warehouse_id),
                    source=src,
                    posting_id=pid,
                    comment=pid,
                    counterparty_id=self._ensure_counterparty_id(session, src),
                    status=ORDER_OPEN,
                    created_at_ts=now,
                    updated_at_ts=now,
                )
                session.add(row)
                session.flush()
            elif row.status not in TERMINAL_STATUSES:
                row.updated_at_ts = now
                if not str(row.comment or "").strip():
                    row.comment = pid
                if not row.counterparty_id:
                    row.counterparty_id = self._ensure_counterparty_id(session, src)
            self._replace_lines(session, row, lines, catalog)
            if row.status not in TERMINAL_STATUSES:
                if self._all_lines_zero(session, int(row.id)):
                    row.status = ORDER_CANCELLED
                    row.updated_at_ts = now
            session.commit()
            session.refresh(row)
            return self._order_row(session, row, load_lines=True)

    def _all_lines_zero(self, session: Session, order_id: int) -> bool:
        rows = session.scalars(
            select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order_id))
        ).all()
        return bool(rows) and all(int(r.quantity) <= 0 for r in rows)

    def _replace_lines(
        self,
        session: Session,
        order: WarehouseOrder,
        lines: list[dict[str, Any]],
        catalog: dict[str, CatalogProduct],
    ) -> None:
        if order.status in TERMINAL_STATUSES:
            return
        by_sku: dict[str, dict[str, Any]] = {}
        for raw in lines:
            sku = str(raw.get("sku") or "").strip()
            if not sku:
                continue
            qty = int(raw.get("quantity") or 0)
            prev = by_sku.get(sku)
            if prev is None:
                by_sku[sku] = {"sku": sku, "quantity": qty, "name": str(raw.get("name") or "")}
            else:
                prev["quantity"] = int(prev["quantity"]) + qty
        existing = {
            str(r.sku): r
            for r in session.scalars(
                select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order.id))
            ).all()
        }
        keep: set[str] = set()
        for sku, payload in by_sku.items():
            keep.add(sku)
            product = catalog.get(sku.casefold())
            name = payload["name"] or (str(product.name) if product else sku)
            product_id = int(product.id) if product else None
            row = existing.get(sku)
            if row is None:
                session.add(
                    WarehouseOrderLine(
                        order_id=int(order.id),
                        sku=sku,
                        product_id=product_id,
                        name=name[:512],
                        quantity=int(payload["quantity"]),
                    )
                )
            else:
                row.quantity = int(payload["quantity"])
                row.name = name[:512]
                row.product_id = product_id
        for sku, row in existing.items():
            if sku not in keep:
                row.quantity = 0

    def mark_cancelled(self, source: str, posting_id: str) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(WarehouseOrder).where(
                    WarehouseOrder.source == str(source),
                    WarehouseOrder.posting_id == str(posting_id),
                )
            )
            if row is None or row.status == ORDER_SHIPPED:
                return None
            if row.status != ORDER_CANCELLED:
                row.status = ORDER_CANCELLED
                row.updated_at_ts = int(time.time())
                session.commit()
                session.refresh(row)
            return self._order_row(session, row, load_lines=True)

    def mark_shipped(self, source: str, posting_id: str) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(WarehouseOrder).where(
                    WarehouseOrder.source == str(source),
                    WarehouseOrder.posting_id == str(posting_id),
                )
            )
            if row is None or row.status == ORDER_SHIPPED:
                return self._order_row(session, row, load_lines=True) if row else None
            row.status = ORDER_SHIPPED
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            return self._order_row(session, row, load_lines=True)

    def set_status(self, order_id: int, status: str) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseOrder, int(order_id))
            if row is None:
                return None
            row.status = str(status)
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            return self._order_row(session, row, load_lines=True)

    def set_packing_job(self, order_ids: Iterable[int], job_id: int | None) -> None:
        ids = [int(i) for i in order_ids]
        if not ids:
            return
        now = int(time.time())
        with Session(self.engine) as session:
            rows = session.scalars(select(WarehouseOrder).where(WarehouseOrder.id.in_(ids))).all()
            for row in rows:
                if row.status in TERMINAL_STATUSES:
                    continue
                row.packing_job_id = int(job_id) if job_id is not None else None
                if job_id is None and row.status == ORDER_IN_WAVE:
                    row.status = ORDER_OPEN
                elif job_id is not None and row.status == ORDER_OPEN:
                    row.status = ORDER_IN_WAVE
                row.updated_at_ts = now
            session.commit()

    def mark_job_packed(self, job_id: int) -> None:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseOrder).where(WarehouseOrder.packing_job_id == int(job_id))
            ).all()
            now = int(time.time())
            for row in rows:
                if row.status == ORDER_IN_WAVE:
                    row.status = ORDER_PACKED
                    row.updated_at_ts = now
            session.commit()

    def clear_packing_job(self, job_id: int) -> None:
        """Отмена волны: только in_wave → open, shipped не трогаем."""
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseOrder).where(WarehouseOrder.packing_job_id == int(job_id))
            ).all()
            now = int(time.time())
            for row in rows:
                if row.status != ORDER_IN_WAVE:
                    continue
                row.status = ORDER_OPEN
                row.packing_job_id = None
                row.updated_at_ts = now
            session.commit()

    def list_by_packing_job(self, job_id: int) -> list[WarehouseOrderRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseOrder).where(WarehouseOrder.packing_job_id == int(job_id))
            ).all()
            return [self._order_row(session, r, load_lines=True) for r in rows]

    def get_by_posting(self, source: str, posting_id: str) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(WarehouseOrder).where(
                    WarehouseOrder.source == str(source),
                    WarehouseOrder.posting_id == str(posting_id),
                )
            )
            if row is None:
                return None
            return self._order_row(session, row, load_lines=True)

    def get_order(self, order_id: int) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseOrder, int(order_id))
            if row is None:
                return None
            return self._order_row(session, row, load_lines=True)

    def _apply_order_filters(
        self,
        stmt,
        *,
        source: str | None = None,
        counterparty_id: int | None = None,
        status: str | None = None,
        q: str = "",
    ):
        if source:
            stmt = stmt.where(WarehouseOrder.source == source)
        if counterparty_id:
            stmt = stmt.where(WarehouseOrder.counterparty_id == int(counterparty_id))
        if status:
            stmt = stmt.where(WarehouseOrder.status == status)
        if q.strip():
            pat = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    WarehouseOrder.number.ilike(pat),
                    WarehouseOrder.comment.ilike(pat),
                    WarehouseOrder.posting_id.ilike(pat),
                )
            )
        return stmt

    def count_orders(
        self,
        *,
        source: str | None = None,
        counterparty_id: int | None = None,
        status: str | None = None,
        q: str = "",
    ) -> int:
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(WarehouseOrder)
            stmt = self._apply_order_filters(
                stmt,
                source=source,
                counterparty_id=counterparty_id,
                status=status,
                q=q,
            )
            return int(session.scalar(stmt) or 0)

    def list_orders(
        self,
        *,
        source: str | None = None,
        counterparty_id: int | None = None,
        status: str | None = None,
        q: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[WarehouseOrderRow]:
        with Session(self.engine) as session:
            stmt = select(WarehouseOrder).order_by(
                WarehouseOrder.created_at_ts.desc(), WarehouseOrder.id.desc()
            )
            stmt = self._apply_order_filters(
                stmt,
                source=source,
                counterparty_id=counterparty_id,
                status=status,
                q=q,
            )
            if offset:
                stmt = stmt.offset(max(0, int(offset)))
            stmt = stmt.limit(max(1, min(int(limit), 2000)))
            rows = session.scalars(stmt).all()
            return [self._order_row(session, r, load_lines=True) for r in rows]

    def list_open_posting_ids(self, source: str) -> list[str]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseOrder.posting_id).where(
                    WarehouseOrder.source == source,
                    WarehouseOrder.status.in_(RESERVE_STATUSES),
                )
            ).all()
            return [str(p) for p in rows]

    def list_active_for_source(self, source: str) -> list[WarehouseOrderRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseOrder).where(
                    WarehouseOrder.source == str(source),
                    WarehouseOrder.status.in_(RESERVE_STATUSES),
                )
            ).all()
            return [self._order_row(session, r, load_lines=True) for r in rows]

    def reserve_qty_by_sku(self, warehouse_id: int | None = None) -> dict[str, int]:
        with Session(self.engine) as session:
            stmt = (
                select(WarehouseOrderLine.sku, func.coalesce(func.sum(WarehouseOrderLine.quantity), 0))
                .join(WarehouseOrder, WarehouseOrder.id == WarehouseOrderLine.order_id)
                .where(WarehouseOrder.status.in_(RESERVE_STATUSES))
            )
            if warehouse_id is not None:
                stmt = stmt.where(WarehouseOrder.warehouse_id == int(warehouse_id))
            stmt = stmt.group_by(WarehouseOrderLine.sku)
            out: dict[str, int] = {}
            for sku, qty in session.execute(stmt).all():
                sku_s = str(sku or "").strip()
                if sku_s:
                    out[sku_s] = int(qty or 0)
            return out

    def backfill_from_order_items(self, warehouse_id: int) -> int:
        """Создаёт заказы из order_items без повторного списания."""
        created = 0
        with Session(self.engine) as session:
            items = session.scalars(select(OrderItem)).all()
            grouped: dict[tuple[str, str], list[OrderItem]] = {}
            for it in items:
                pid = posting_id_from_external(it.external_order_id)
                if not pid:
                    continue
                grouped.setdefault((str(it.source), pid), []).append(it)
            existing = {
                (r.source, r.posting_id)
                for r in session.scalars(select(WarehouseOrder)).all()
            }
        for (source, posting_id), rows in grouped.items():
            if (source, posting_id) in existing:
                continue
            states = {str(r.state) for r in rows}
            if states <= {"shipped"} or "shipped" in states and "added" not in states:
                status = ORDER_SHIPPED
            elif states <= {"cancelled"} or "cancelled" in states and "added" not in states:
                status = ORDER_CANCELLED
            else:
                status = ORDER_OPEN
            lines = [{"sku": r.sku, "quantity": int(r.quantity), "name": ""} for r in rows if str(r.state) != "cancelled" or status != ORDER_OPEN]
            if status == ORDER_OPEN:
                lines = [{"sku": r.sku, "quantity": int(r.quantity), "name": ""} for r in rows if str(r.state) == "added"]
            elif status == ORDER_SHIPPED:
                lines = [{"sku": r.sku, "quantity": int(r.quantity), "name": ""} for r in rows]
            else:
                lines = [{"sku": r.sku, "quantity": int(r.quantity), "name": ""} for r in rows]
            if not lines:
                continue
            with Session(self.engine) as session:
                if session.scalar(
                    select(WarehouseOrder.id).where(
                        WarehouseOrder.source == source,
                        WarehouseOrder.posting_id == posting_id,
                    )
                ):
                    continue
                now = int(time.time())
                catalog = {
                    str(p.sku or "").strip().casefold(): p
                    for p in session.scalars(select(CatalogProduct)).all()
                    if str(p.sku or "").strip()
                }
                order = WarehouseOrder(
                    number=self.next_number(session),
                    warehouse_id=int(warehouse_id),
                    source=source,
                    posting_id=posting_id,
                    comment=posting_id,
                    counterparty_id=self._ensure_counterparty_id(session, source),
                    status=status,
                    created_at_ts=min((int(r.first_seen_ts) or now) for r in rows) or now,
                    updated_at_ts=now,
                )
                session.add(order)
                session.flush()
                self._replace_lines(session, order, lines, catalog)
                # _replace_lines skips terminal — force lines for backfill
                if status in TERMINAL_STATUSES:
                    for i, payload in enumerate(lines):
                        sku = str(payload["sku"]).strip()
                        product = catalog.get(sku.casefold())
                        session.add(
                            WarehouseOrderLine(
                                order_id=int(order.id),
                                sku=sku,
                                product_id=int(product.id) if product else None,
                                name=str(product.name)[:512] if product else sku,
                                quantity=int(payload["quantity"]),
                            )
                        )
                session.commit()
                created += 1
        return created

    def to_dict(self, row: WarehouseOrderRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "number": row.number,
            "warehouse_id": row.warehouse_id,
            "source": row.source,
            "posting_id": row.posting_id,
            "counterparty_id": row.counterparty_id,
            "counterparty_name": row.counterparty_name,
            "comment": row.comment,
            "status": row.status,
            "packing_job_id": row.packing_job_id,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
            "lines": [
                {
                    "id": ln.id,
                    "sku": ln.sku,
                    "product_id": ln.product_id,
                    "name": ln.name,
                    "quantity": ln.quantity,
                }
                for ln in row.lines
            ],
        }

    def _order_row(self, session: Session, row: WarehouseOrder, *, load_lines: bool) -> WarehouseOrderRow:
        lines: list[WarehouseOrderLineRow] = []
        if load_lines:
            for it in session.scalars(
                select(WarehouseOrderLine)
                .where(WarehouseOrderLine.order_id == int(row.id))
                .order_by(WarehouseOrderLine.id)
            ).all():
                lines.append(
                    WarehouseOrderLineRow(
                        id=int(it.id),
                        sku=str(it.sku),
                        product_id=int(it.product_id) if it.product_id else None,
                        name=str(it.name or ""),
                        quantity=int(it.quantity),
                    )
                )
        return WarehouseOrderRow(
            id=int(row.id),
            number=str(row.number),
            warehouse_id=int(row.warehouse_id),
            source=str(row.source),
            posting_id=str(row.posting_id),
            counterparty_id=int(row.counterparty_id) if row.counterparty_id else None,
            counterparty_name=self._counterparty_name(session, row),
            comment=str(row.comment or ""),
            status=str(row.status),
            packing_job_id=int(row.packing_job_id) if row.packing_job_id else None,
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
            lines=lines,
        )
