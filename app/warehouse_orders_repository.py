"""Заказы покупателей: общий документ, не карточка маркетплейса.

Для заказов с МП номер отправления пишется в комментарий, площадка — контрагент CRM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import Boolean, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, or_, select
from sqlalchemy.exc import IntegrityError
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
SOURCE_MANUAL = "manual"
SOURCE_COUNTERPARTY_NAMES = {
    SOURCE_OZON: "Ozon",
    SOURCE_WB: "Wildberries",
    SOURCE_YM: "Яндекс Маркет",
}
ORDER_KIND_SALE = "sale"
ORDER_KIND_COMMISSION = "commission"
DEFAULT_VAT_RATE = Decimal("22")
RETAIL_PRICE_TYPE_NAME = "Розничная цена"
COST_PRICE_TYPE_NAME = "Себестоимость"
MONEY = Numeric(12, 2)


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
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ORDER_OPEN)
    order_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=ORDER_KIND_SALE)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=DEFAULT_VAT_RATE)
    lines_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    packing_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buyer_paid: Mapped[float | None] = mapped_column(Float, nullable=True)
    cabinet_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    listed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mp_hold: Mapped[float | None] = mapped_column(Float, nullable=True)
    mp_hold_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    mp_hold_note: Mapped[str] = mapped_column(String(512), nullable=False, default="")
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
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    unit_price_net: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    vat_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    commission: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    buyer_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    subsidy: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


@dataclass
class WarehouseOrderLineRow:
    id: int
    sku: str
    product_id: int | None
    name: str
    quantity: int
    unit_price: Decimal | None = None
    unit_price_net: Decimal | None = None
    vat_amount: Decimal | None = None
    cost: Decimal | None = None
    commission: Decimal | None = None
    buyer_price: Decimal | None = None
    subsidy: Decimal | None = None


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
    order_kind: str = ORDER_KIND_SALE
    vat_rate: Decimal = DEFAULT_VAT_RATE
    lines_manual: bool = False
    buyer_paid: float | None = None
    cabinet_price: float | None = None
    listed_price: float | None = None
    mp_hold: float | None = None
    mp_hold_kind: str = ""
    mp_hold_note: str = ""
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

        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        if "warehouse_orders" not in tables:
            return
        cols = {c["name"] for c in inspector.get_columns("warehouse_orders")}
        statements: list[str] = []
        added_kind = False
        added_line_prices = False
        if "comment" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS comment TEXT NOT NULL DEFAULT ''"
            )
        else:
            statements.append("ALTER TABLE warehouse_orders ALTER COLUMN comment TYPE TEXT")
        if "counterparty_id" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS counterparty_id INTEGER"
            )
        if "buyer_paid" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS buyer_paid DOUBLE PRECISION"
            )
        if "cabinet_price" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS cabinet_price DOUBLE PRECISION"
            )
        if "listed_price" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS listed_price DOUBLE PRECISION"
            )
        if "mp_hold" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS mp_hold DOUBLE PRECISION"
            )
        if "mp_hold_kind" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS mp_hold_kind "
                "VARCHAR(32) NOT NULL DEFAULT ''"
            )
        if "mp_hold_note" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS mp_hold_note "
                "VARCHAR(512) NOT NULL DEFAULT ''"
            )
        if "order_kind" not in cols:
            added_kind = True
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS order_kind "
                "VARCHAR(16) NOT NULL DEFAULT 'sale'"
            )
        if "vat_rate" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS vat_rate "
                "NUMERIC(5,2) NOT NULL DEFAULT 22"
            )
        if "lines_manual" not in cols:
            statements.append(
                "ALTER TABLE warehouse_orders ADD COLUMN IF NOT EXISTS lines_manual "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
        line_cols: set[str] = set()
        if "warehouse_order_lines" in tables:
            line_cols = {c["name"] for c in inspector.get_columns("warehouse_order_lines")}
            for col in (
                "unit_price",
                "unit_price_net",
                "vat_amount",
                "cost",
                "commission",
                "buyer_price",
                "subsidy",
            ):
                if col not in line_cols:
                    added_line_prices = True
                    statements.append(
                        f"ALTER TABLE warehouse_order_lines ADD COLUMN IF NOT EXISTS {col} NUMERIC(12,2)"
                    )
        with Session(self.engine) as session:
            for sql in statements:
                session.execute(text(sql))
            if added_kind:
                session.execute(
                    text(
                        "UPDATE warehouse_orders SET order_kind = 'commission' "
                        "WHERE source IN ('ozon', 'wildberries', 'yandex_market')"
                    )
                )
            session.commit()
        if added_line_prices:
            self._backfill_line_prices()

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

    def _money_col(self, value: Any):
        from app.warehouse_order_pricing import parse_money

        return parse_money(value)

    def _vat_parts(self, unit_price: Any, vat_rate: Any) -> tuple[Any, Any]:
        from app.warehouse_order_pricing import DEFAULT_VAT_RATE, parse_money, split_vat

        rate = parse_money(vat_rate) or DEFAULT_VAT_RATE
        return split_vat(parse_money(unit_price), rate)

    def _apply_vat_on_line(self, line: WarehouseOrderLine, vat_rate: Any) -> None:
        net, vat = self._vat_parts(line.unit_price, vat_rate)
        line.unit_price_net = net
        line.vat_amount = vat

    def _catalog_by_sku(self, session: Session) -> dict[str, CatalogProduct]:
        return {
            str(p.sku or "").strip().casefold(): p
            for p in session.scalars(select(CatalogProduct)).all()
            if str(p.sku or "").strip()
        }

    def _price_map_for_type(self, session: Session, price_type_id: int | None) -> dict[int, Any]:
        from app.catalog_repository import CatalogProductPrice
        from app.warehouse_order_pricing import parse_money

        if not price_type_id:
            return {}
        rows = session.scalars(
            select(CatalogProductPrice).where(CatalogProductPrice.price_type_id == int(price_type_id))
        ).all()
        out: dict[int, Any] = {}
        for row in rows:
            amount = parse_money(row.price)
            if amount is not None:
                out[int(row.product_id)] = amount
        return out

    def _cost_price_type_id(self, session: Session) -> int | None:
        from app.crm_repository import CrmPriceType

        rows = session.scalars(
            select(CrmPriceType).order_by(CrmPriceType.sort_order, CrmPriceType.id)
        ).all()
        for row in rows:
            if bool(getattr(row, "is_cost", False)):
                return int(row.id)
        return None

    def _price_type_id_by_name(self, session: Session, name: str) -> int | None:
        from app.crm_repository import CrmPriceType

        needle = str(name or "").strip().casefold()
        if not needle:
            return None
        for row in session.scalars(select(CrmPriceType)).all():
            if str(row.name or "").strip().casefold() == needle:
                return int(row.id)
        return None

    def _ensure_cost_price_type(self, session: Session) -> int | None:
        from app.crm_repository import CrmPriceType

        existing = self._cost_price_type_id(session)
        if existing:
            return existing
        if not self._crm_ready(session):
            return None
        row = None
        for item in session.scalars(select(CrmPriceType)).all():
            if str(item.name or "").strip().casefold() == COST_PRICE_TYPE_NAME.casefold():
                row = item
                break
        if row is None:
            max_order = session.scalar(select(func.max(CrmPriceType.sort_order))) or 0
            row = CrmPriceType(name=COST_PRICE_TYPE_NAME, sort_order=int(max_order) + 1)
            session.add(row)
            session.flush()
        row.is_cost = True
        session.flush()
        return int(row.id)

    def _backfill_line_prices(self) -> None:
        from app.warehouse_order_pricing import DEFAULT_VAT_RATE, parse_money

        with Session(self.engine) as session:
            if not self._crm_ready(session):
                return
            cost_type_id = self._ensure_cost_price_type(session)
            retail_type_id = self._price_type_id_by_name(session, RETAIL_PRICE_TYPE_NAME)
            cost_map = self._price_map_for_type(session, cost_type_id)
            retail_map = self._price_map_for_type(session, retail_type_id)
            catalog = {int(p.id): p for p in session.scalars(select(CatalogProduct)).all()}
            sku_to_product = {
                str(p.sku or "").strip().casefold(): p
                for p in catalog.values()
                if str(p.sku or "").strip()
            }
            orders = {int(o.id): o for o in session.scalars(select(WarehouseOrder)).all()}
            lines = session.scalars(select(WarehouseOrderLine)).all()
            for line in lines:
                order = orders.get(int(line.order_id))
                if order is None:
                    continue
                product = None
                if line.product_id:
                    product = catalog.get(int(line.product_id))
                if product is None:
                    product = sku_to_product.get(str(line.sku or "").strip().casefold())
                    if product is not None:
                        line.product_id = int(product.id)
                pid = int(product.id) if product is not None else None
                if line.cost is None:
                    if pid is not None and pid in cost_map:
                        line.cost = cost_map[pid]
                    else:
                        line.cost = parse_money("0")
                if str(order.source) != SOURCE_YM and line.unit_price is None and pid is not None:
                    if pid in retail_map:
                        line.unit_price = retail_map[pid]
                self._apply_vat_on_line(line, getattr(order, "vat_rate", None) or DEFAULT_VAT_RATE)
            session.commit()

    def fill_empty_costs(self, order_id: int) -> WarehouseOrderRow | None:
        from app.warehouse_order_pricing import parse_money

        with Session(self.engine) as session:
            order = session.get(WarehouseOrder, int(order_id))
            if order is None:
                return None
            cost_type_id = self._ensure_cost_price_type(session) if self._crm_ready(session) else None
            cost_map = self._price_map_for_type(session, cost_type_id)
            catalog = self._catalog_by_sku(session)
            lines = session.scalars(
                select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order.id))
            ).all()
            changed = False
            for line in lines:
                if line.cost is not None:
                    continue
                product = None
                if line.product_id:
                    product = session.get(CatalogProduct, int(line.product_id))
                if product is None:
                    product = catalog.get(str(line.sku or "").strip().casefold())
                    if product is not None:
                        line.product_id = int(product.id)
                pid = int(product.id) if product is not None else None
                if pid is not None and pid in cost_map:
                    line.cost = cost_map[pid]
                else:
                    line.cost = parse_money("0")
                changed = True
            if changed:
                session.commit()
            session.refresh(order)
            return self._order_row(session, order, load_lines=True)

    def apply_empty_line_money(
        self,
        order_id: int,
        patches: list[dict[str, Any]],
        *,
        comment: str | None = None,
    ) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            order = session.get(WarehouseOrder, int(order_id))
            if order is None:
                return None
            by_sku = {
                str(r.sku): r
                for r in session.scalars(
                    select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order.id))
                ).all()
            }
            for raw in patches:
                sku = str(raw.get("sku") or "").strip()
                line = by_sku.get(sku)
                if line is None:
                    continue
                if "unit_price" in raw and line.unit_price is None:
                    line.unit_price = self._money_col(raw.get("unit_price"))
                    self._apply_vat_on_line(line, order.vat_rate)
                if "cost" in raw and line.cost is None:
                    line.cost = self._money_col(raw.get("cost"))
                if "commission" in raw and line.commission is None:
                    line.commission = self._money_col(raw.get("commission"))
                if "buyer_price" in raw and line.buyer_price is None:
                    line.buyer_price = self._money_col(raw.get("buyer_price"))
                if "subsidy" in raw and line.subsidy is None:
                    line.subsidy = self._money_col(raw.get("subsidy"))
            if comment is not None:
                order.comment = str(comment)
            order.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(order)
            return self._order_row(session, order, load_lines=True)

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
            catalog = self._catalog_by_sku(session)
            kind = (
                ORDER_KIND_COMMISSION
                if src in SOURCE_COUNTERPARTY_NAMES
                else ORDER_KIND_SALE
            )
            if row is None:
                row = WarehouseOrder(
                    number=self.next_number(session),
                    warehouse_id=int(warehouse_id),
                    source=src,
                    posting_id=pid,
                    comment=pid,
                    counterparty_id=self._ensure_counterparty_id(session, src),
                    status=ORDER_OPEN,
                    order_kind=kind,
                    vat_rate=DEFAULT_VAT_RATE,
                    lines_manual=False,
                    created_at_ts=now,
                    updated_at_ts=now,
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    row = session.scalar(
                        select(WarehouseOrder).where(
                            WarehouseOrder.source == src,
                            WarehouseOrder.posting_id == pid,
                        )
                    )
                    catalog = self._catalog_by_sku(session)
                    if row is None:
                        row = WarehouseOrder(
                            number=self.next_number(session),
                            warehouse_id=int(warehouse_id),
                            source=src,
                            posting_id=pid,
                            comment=pid,
                            counterparty_id=self._ensure_counterparty_id(session, src),
                            status=ORDER_OPEN,
                            order_kind=kind,
                            vat_rate=DEFAULT_VAT_RATE,
                            lines_manual=False,
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
            created = self._order_row(session, row, load_lines=True)
        filled = self.fill_empty_costs(created.id)
        return filled or created

    def set_order_money(self, order_id: int, money: dict[str, Any] | None) -> None:
        if not money:
            return
        with Session(self.engine) as session:
            row = session.get(WarehouseOrder, int(order_id))
            if row is None:
                return
            row.buyer_paid = money.get("buyer_paid")
            row.cabinet_price = money.get("cabinet_price")
            row.listed_price = money.get("listed_price")
            row.mp_hold = money.get("mp_hold")
            row.mp_hold_kind = str(money.get("mp_hold_kind") or "")[:32]
            row.mp_hold_note = str(money.get("mp_hold_note") or "")[:512]
            row.updated_at_ts = int(time.time())
            session.commit()

    def _merge_line_payloads(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_sku: dict[str, dict[str, Any]] = {}
        for raw in lines:
            if not isinstance(raw, dict):
                continue
            sku = str(raw.get("sku") or "").strip()
            if not sku:
                continue
            qty = int(raw.get("quantity") or 0)
            prev = by_sku.get(sku)
            if prev is None:
                item = dict(raw)
                item["sku"] = sku
                item["quantity"] = qty
                by_sku[sku] = item
            else:
                prev["quantity"] = int(prev.get("quantity") or 0) + qty
        return list(by_sku.values())

    def _write_line_money(self, line: WarehouseOrderLine, payload: dict[str, Any], vat_rate) -> None:
        from app.warehouse_order_pricing import parse_money

        if "unit_price" in payload:
            line.unit_price = parse_money(payload.get("unit_price"))
            self._apply_vat_on_line(line, vat_rate)
        if "cost" in payload:
            line.cost = parse_money(payload.get("cost"))
        if "commission" in payload:
            line.commission = parse_money(payload.get("commission"))
        if "buyer_price" in payload:
            line.buyer_price = parse_money(payload.get("buyer_price"))
        if "subsidy" in payload:
            line.subsidy = parse_money(payload.get("subsidy"))

    def _set_manual_lines(
        self,
        session: Session,
        order: WarehouseOrder,
        lines: list[dict[str, Any]],
        catalog: dict[str, CatalogProduct],
        *,
        lock_lines: bool,
    ) -> None:
        merged = self._merge_line_payloads(lines)
        existing = {
            str(r.sku): r
            for r in session.scalars(
                select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(order.id))
            ).all()
        }
        keep: set[str] = set()
        for payload in merged:
            sku = str(payload["sku"])
            keep.add(sku)
            product = catalog.get(sku.casefold())
            name = str(payload.get("name") or "") or (str(product.name) if product else sku)
            product_id = payload.get("product_id")
            try:
                product_id = int(product_id) if product_id not in (None, "") else None
            except (TypeError, ValueError):
                product_id = None
            if product_id is None and product is not None:
                product_id = int(product.id)
            row = existing.get(sku)
            if row is None:
                row = WarehouseOrderLine(
                    order_id=int(order.id),
                    sku=sku,
                    product_id=product_id,
                    name=name[:512],
                    quantity=int(payload.get("quantity") or 0),
                )
                session.add(row)
                session.flush()
            else:
                row.quantity = int(payload.get("quantity") or 0)
                row.name = name[:512]
                row.product_id = product_id
            self._write_line_money(row, payload, order.vat_rate)
            if row.unit_price is not None:
                self._apply_vat_on_line(row, order.vat_rate)
        for sku, row in existing.items():
            if sku not in keep:
                session.delete(row)
        if lock_lines:
            order.lines_manual = True

    def create_manual_order(self, payload: dict[str, Any]) -> WarehouseOrderRow:
        from app.warehouse_order_pricing import parse_money

        now = int(time.time())
        status = str(payload.get("status") or ORDER_OPEN).strip() or ORDER_OPEN
        kind = str(payload.get("order_kind") or ORDER_KIND_SALE).strip() or ORDER_KIND_SALE
        if kind not in {ORDER_KIND_SALE, ORDER_KIND_COMMISSION}:
            raise ValueError("Некорректный тип заказа")
        vat_rate = parse_money(payload.get("vat_rate")) or DEFAULT_VAT_RATE
        try:
            warehouse_id = int(payload.get("warehouse_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Склад обязателен") from exc
        try:
            counterparty_id = int(payload.get("counterparty_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Контрагент обязателен") from exc
        raw_lines = payload.get("lines") or []
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError("Добавьте хотя бы одну строку")
        with Session(self.engine) as session:
            number = self.next_number(session)
            row = WarehouseOrder(
                number=number,
                warehouse_id=warehouse_id,
                source=SOURCE_MANUAL,
                posting_id=f"M-{number}",
                counterparty_id=counterparty_id,
                comment=str(payload.get("comment") or ""),
                status=status,
                order_kind=kind,
                vat_rate=vat_rate,
                lines_manual=True,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.flush()
            catalog = self._catalog_by_sku(session)
            self._set_manual_lines(session, row, raw_lines, catalog, lock_lines=True)
            session.commit()
            session.refresh(row)
            created = self._order_row(session, row, load_lines=True)
        filled = self.fill_empty_costs(created.id)
        return filled or created

    def update_order(self, order_id: int, payload: dict[str, Any]) -> WarehouseOrderRow | None:
        from app.warehouse_order_pricing import parse_money

        with Session(self.engine) as session:
            row = session.get(WarehouseOrder, int(order_id))
            if row is None:
                return None
            if "status" in payload and payload.get("status") is not None:
                row.status = str(payload.get("status") or "").strip() or row.status
            if "comment" in payload:
                row.comment = str(payload.get("comment") or "")
            if "order_kind" in payload and payload.get("order_kind") is not None:
                kind = str(payload.get("order_kind") or "").strip()
                if kind not in {ORDER_KIND_SALE, ORDER_KIND_COMMISSION}:
                    raise ValueError("Некорректный тип заказа")
                row.order_kind = kind
            if "counterparty_id" in payload and payload.get("counterparty_id") not in (None, ""):
                row.counterparty_id = int(payload.get("counterparty_id"))
            vat_changed = False
            if "vat_rate" in payload and payload.get("vat_rate") not in (None, ""):
                row.vat_rate = parse_money(payload.get("vat_rate")) or DEFAULT_VAT_RATE
                vat_changed = True
            catalog = self._catalog_by_sku(session)
            if "lines" in payload:
                raw_lines = payload.get("lines") or []
                if not isinstance(raw_lines, list):
                    raise ValueError("lines должен быть массивом")
                self._set_manual_lines(session, row, raw_lines, catalog, lock_lines=True)
            elif vat_changed:
                for line in session.scalars(
                    select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(row.id))
                ).all():
                    self._apply_vat_on_line(line, row.vat_rate)
            row.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(row)
            updated = self._order_row(session, row, load_lines=True)
        filled = self.fill_empty_costs(updated.id)
        return filled or updated

    def apply_price_type(self, order_id: int, price_type_id: int) -> WarehouseOrderRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseOrder, int(order_id))
            if row is None:
                return None
            price_map = self._price_map_for_type(session, int(price_type_id))
            catalog = self._catalog_by_sku(session)
            lines = session.scalars(
                select(WarehouseOrderLine).where(WarehouseOrderLine.order_id == int(row.id))
            ).all()
            for line in lines:
                pid = int(line.product_id) if line.product_id else None
                if pid is None:
                    product = catalog.get(str(line.sku or "").strip().casefold())
                    if product is not None:
                        pid = int(product.id)
                        line.product_id = pid
                line.unit_price = price_map.get(pid, Decimal("0.00")) if pid else Decimal("0.00")
                self._apply_vat_on_line(line, row.vat_rate)
            row.updated_at_ts = int(time.time())
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
        if bool(getattr(order, "lines_manual", False)):
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
        """Создаёт заказы из order_items без повторного списания.

        Только разовый прогон (`python tools/backfill_warehouse_orders.py`), не старт web/sync.
        """
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
                    order_kind=ORDER_KIND_COMMISSION if source in SOURCE_COUNTERPARTY_NAMES else ORDER_KIND_SALE,
                    vat_rate=DEFAULT_VAT_RATE,
                    created_at_ts=min((int(r.first_seen_ts) or now) for r in rows) or now,
                    updated_at_ts=now,
                )
                session.add(order)
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    continue
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
                try:
                    session.commit()
                    created += 1
                except IntegrityError:
                    session.rollback()
        return created

    def to_dict(self, row: WarehouseOrderRow) -> dict[str, Any]:
        from app.warehouse_order_pricing import money_json

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
            "order_kind": row.order_kind,
            "vat_rate": money_json(row.vat_rate),
            "lines_manual": bool(row.lines_manual),
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
                    "unit_price": money_json(ln.unit_price),
                    "unit_price_net": money_json(ln.unit_price_net),
                    "vat_amount": money_json(ln.vat_amount),
                    "cost": money_json(ln.cost),
                    "commission": money_json(ln.commission),
                    "buyer_price": money_json(ln.buyer_price),
                    "subsidy": money_json(ln.subsidy),
                }
                for ln in row.lines
            ],
        }

    def _order_row(self, session: Session, row: WarehouseOrder, *, load_lines: bool) -> WarehouseOrderRow:
        from app.warehouse_order_pricing import parse_money

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
                        unit_price=parse_money(it.unit_price),
                        unit_price_net=parse_money(it.unit_price_net),
                        vat_amount=parse_money(it.vat_amount),
                        cost=parse_money(it.cost),
                        commission=parse_money(it.commission),
                        buyer_price=parse_money(it.buyer_price),
                        subsidy=parse_money(it.subsidy),
                    )
                )
        vat_rate = parse_money(getattr(row, "vat_rate", None)) or DEFAULT_VAT_RATE
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
            order_kind=str(getattr(row, "order_kind", None) or ORDER_KIND_SALE),
            vat_rate=vat_rate,
            lines_manual=bool(getattr(row, "lines_manual", False)),
            buyer_paid=float(row.buyer_paid) if row.buyer_paid is not None else None,
            cabinet_price=float(row.cabinet_price) if row.cabinet_price is not None else None,
            listed_price=float(row.listed_price) if row.listed_price is not None else None,
            mp_hold=float(row.mp_hold) if row.mp_hold is not None else None,
            mp_hold_kind=str(row.mp_hold_kind or ""),
            mp_hold_note=str(row.mp_hold_note or ""),
            lines=lines,
        )
