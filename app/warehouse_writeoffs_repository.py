"""Списания товаров со склада (новая панель /warehouse)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, delete, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogProduct, _parse_price
from app.storage_warehouse_repository import StorageWarehouse, StorageWarehouseRepository


class _Base(DeclarativeBase):
    pass


class WarehouseWriteoff(_Base):
    __tablename__ = "warehouse_writeoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_sum: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseWriteoffItem(_Base):
    __tablename__ = "warehouse_writeoff_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    writeoff_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_writeoffs.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    is_kit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    line_sum: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class WriteoffItemRow:
    id: Optional[int]
    product_id: int
    sku: str
    name: str
    code: str
    image_url: str
    is_kit: bool
    quantity: int
    unit_price: Optional[str]
    line_sum: Optional[str]
    sort_order: int = 0


@dataclass
class WriteoffRow:
    id: int
    title: str
    display_name: str
    warehouse_id: int
    warehouse_name: str
    comment: str
    total_quantity: int
    total_sum: str
    created_at_ts: int
    updated_at_ts: int
    items: list[WriteoffItemRow] = field(default_factory=list)


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


def _display_name(title: str) -> str:
    t = str(title or "").strip()
    if not t:
        return "Списание"
    return f"Списание {t}"


def _truncate_comment(comment: str, limit: int = 120) -> str:
    c = str(comment or "").strip()
    if len(c) <= limit:
        return c
    return c[: max(0, limit - 3)].rstrip() + "..."


class WarehouseWriteoffsRepository:
    def __init__(self, db_url: str, storage_repo: StorageWarehouseRepository) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)
        self.storage_repo = storage_repo

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._migrate_total_sum_column()
        self._backfill_total_sums()

    def _migrate_total_sum_column(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_writeoffs" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_writeoffs")}
        if "total_sum" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE warehouse_writeoffs "
                    "ADD COLUMN total_sum VARCHAR(32) NOT NULL DEFAULT '0.00'"
                )
            )
            session.commit()

    def _backfill_total_sums(self) -> None:
        with Session(self.engine) as session:
            writeoffs = session.scalars(select(WarehouseWriteoff)).all()
            for writeoff in writeoffs:
                if str(writeoff.total_sum or "").strip() not in ("", "0", "0.00"):
                    continue
                items = session.scalars(
                    select(WarehouseWriteoffItem).where(
                        WarehouseWriteoffItem.writeoff_id == int(writeoff.id)
                    )
                ).all()
                writeoff.total_sum = _calc_lines_total_sum(
                    [str(it.line_sum or "") for it in items]
                )
            session.commit()

    def list_writeoffs(self, filters: dict[str, str]) -> list[WriteoffRow]:
        with Session(self.engine) as session:
            q = select(WarehouseWriteoff).order_by(
                WarehouseWriteoff.created_at_ts.desc(), WarehouseWriteoff.id.desc()
            )
            conds = self._list_filter_conditions(filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            return [self._writeoff_row(session, r, load_items=False) for r in rows]

    def get_writeoff(self, writeoff_id: int) -> WriteoffRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseWriteoff, int(writeoff_id))
            if row is None:
                return None
            return self._writeoff_row(session, row, load_items=True)

    def create_writeoff(self, data: dict[str, Any]) -> WriteoffRow:
        return self._save_writeoff(None, data)

    def update_writeoff(self, writeoff_id: int, data: dict[str, Any]) -> WriteoffRow | None:
        with Session(self.engine) as session:
            if session.get(WarehouseWriteoff, int(writeoff_id)) is None:
                return None
        return self._save_writeoff(int(writeoff_id), data)

    def delete_writeoff(self, writeoff_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WarehouseWriteoff, int(writeoff_id))
            if row is None:
                return False
            old_items = session.scalars(
                select(WarehouseWriteoffItem).where(WarehouseWriteoffItem.writeoff_id == int(writeoff_id))
            ).all()
            deltas = self._items_to_stock_deltas(old_items)
            wh_id = int(row.warehouse_id)
            session.delete(row)
            session.commit()
        self._apply_stock_deltas(wh_id, deltas, multiplier=1)
        return True

    def writeoff_to_dict(self, row: WriteoffRow, *, include_items: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": row.id,
            "title": row.title,
            "display_name": row.display_name,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "comment": row.comment,
            "comment_short": _truncate_comment(row.comment),
            "total_quantity": row.total_quantity,
            "total_sum": row.total_sum,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }
        if include_items:
            d["items"] = [self._item_to_dict(i) for i in row.items]
        return d

    def _item_to_dict(self, item: WriteoffItemRow) -> dict[str, Any]:
        return {
            "id": item.id,
            "product_id": item.product_id,
            "sku": item.sku,
            "name": item.name,
            "code": item.code,
            "image_url": item.image_url,
            "is_kit": item.is_kit,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "line_sum": item.line_sum,
            "sort_order": item.sort_order,
        }

    def _save_writeoff(self, writeoff_id: int | None, data: dict[str, Any]) -> WriteoffRow:
        title = str(data.get("title") or "").strip()
        if not title:
            raise ValueError("Название списания обязательно")
        try:
            warehouse_id = int(data.get("warehouse_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Выберите склад") from exc
        comment = str(data.get("comment") or "").strip()[:2048]
        items_raw = data.get("items")
        if not isinstance(items_raw, list) or not items_raw:
            raise ValueError("Добавьте хотя бы один товар")
        items_norm = self._normalize_items(items_raw)
        now = int(time.time())

        with Session(self.engine) as session:
            if session.get(StorageWarehouse, warehouse_id) is None:
                raise ValueError("Склад не найден")

            old_wh_id: int | None = None
            old_deltas: dict[str, int] = {}
            if writeoff_id is not None:
                old = session.get(WarehouseWriteoff, int(writeoff_id))
                if old is None:
                    raise ValueError("Списание не найдено")
                old_wh_id = int(old.warehouse_id)
                old_rows = session.scalars(
                    select(WarehouseWriteoffItem).where(
                        WarehouseWriteoffItem.writeoff_id == int(writeoff_id)
                    )
                ).all()
                old_deltas = self._items_to_stock_deltas(old_rows)

            if writeoff_id is None:
                writeoff = WarehouseWriteoff(
                    title=title[:256],
                    warehouse_id=warehouse_id,
                    comment=comment,
                    created_at_ts=now,
                )
                session.add(writeoff)
            else:
                writeoff = session.get(WarehouseWriteoff, int(writeoff_id))
                if writeoff is None:
                    raise ValueError("Списание не найдено")
                writeoff.title = title[:256]
                writeoff.warehouse_id = warehouse_id
                writeoff.comment = comment
                session.execute(
                    delete(WarehouseWriteoffItem).where(
                        WarehouseWriteoffItem.writeoff_id == int(writeoff_id)
                    )
                )

            session.flush()
            total_qty = 0
            for i, item in enumerate(items_norm):
                product = session.get(CatalogProduct, int(item["product_id"]))
                if product is None:
                    raise ValueError(f"Товар id={item['product_id']} не найден")
                qty = int(item["quantity"])
                total_qty += qty
                session.add(
                    WarehouseWriteoffItem(
                        writeoff_id=int(writeoff.id),
                        product_id=int(product.id),
                        sku=str(product.sku),
                        name=str(product.name)[:512],
                        image_url=str(product.image_url or "")[:2048],
                        is_kit=bool(product.is_kit),
                        quantity=qty,
                        unit_price=item.get("unit_price") or "",
                        line_sum=item.get("line_sum") or "",
                        sort_order=i,
                    )
                )
            writeoff.total_quantity = total_qty
            writeoff.total_sum = _calc_items_total_sum(items_norm)
            writeoff.updated_at_ts = now
            session.commit()
            session.refresh(writeoff)
            result = self._writeoff_row(session, writeoff, load_items=True)

        new_deltas = self._items_to_stock_deltas_from_rows(items_norm)
        if writeoff_id is None:
            self._apply_stock_deltas(warehouse_id, new_deltas, multiplier=-1)
        elif old_wh_id == warehouse_id:
            net: dict[str, int] = {}
            for sku in set(old_deltas) | set(new_deltas):
                delta = new_deltas.get(sku, 0) - old_deltas.get(sku, 0)
                if delta:
                    net[sku] = delta
            self._apply_stock_deltas(warehouse_id, net, multiplier=-1)
        else:
            if old_wh_id is not None:
                self._apply_stock_deltas(old_wh_id, old_deltas, multiplier=1)
            self._apply_stock_deltas(warehouse_id, new_deltas, multiplier=-1)
        return result

    def _normalize_items(self, items_raw: list) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw in items_raw:
            if not isinstance(raw, dict):
                continue
            try:
                product_id = int(raw.get("product_id"))
            except (TypeError, ValueError):
                continue
            if product_id in seen:
                raise ValueError("Один и тот же товар указан несколько раз")
            seen.add(product_id)
            try:
                qty = int(raw.get("quantity"))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                raise ValueError("Количество должно быть больше нуля")
            unit_price = _parse_price_optional(raw.get("unit_price"))
            line_sum = _parse_price_optional(raw.get("line_sum"))
            if unit_price is None and line_sum is not None:
                unit_price = _price_from_sum(line_sum, qty)
            if line_sum is None and unit_price is not None:
                line_sum = _sum_from_price(unit_price, qty)
            out.append(
                {
                    "product_id": product_id,
                    "quantity": qty,
                    "unit_price": unit_price or "",
                    "line_sum": line_sum or "",
                }
            )
        if not out:
            raise ValueError("Добавьте хотя бы один товар")
        return out

    def _items_to_stock_deltas(self, rows: list[WarehouseWriteoffItem]) -> dict[str, int]:
        deltas: dict[str, int] = {}
        for row in rows:
            sku = str(row.sku or "").strip()
            if not sku:
                continue
            deltas[sku] = deltas.get(sku, 0) + int(row.quantity)
        return deltas

    def _items_to_stock_deltas_from_rows(self, items: list[dict[str, Any]]) -> dict[str, int]:
        deltas: dict[str, int] = {}
        with Session(self.engine) as session:
            for item in items:
                product = session.get(CatalogProduct, int(item["product_id"]))
                if product is None:
                    continue
                sku = str(product.sku or "").strip()
                if not sku:
                    continue
                deltas[sku] = deltas.get(sku, 0) + int(item["quantity"])
        return deltas

    def _apply_stock_deltas(self, warehouse_id: int, deltas: dict[str, int], *, multiplier: int) -> None:
        if not deltas:
            return
        applied: dict[str, int] = {}
        for sku, qty in deltas.items():
            sku_n = str(sku or "").strip()
            if not sku_n:
                continue
            delta = int(qty) * int(multiplier)
            if not delta:
                continue
            applied[sku_n] = delta
        if applied:
            self.storage_repo.adjust_stocks(int(warehouse_id), applied)

    def _writeoff_row(
        self, session: Session, row: WarehouseWriteoff, *, load_items: bool
    ) -> WriteoffRow:
        wh_name = ""
        wh = session.get(StorageWarehouse, int(row.warehouse_id))
        if wh:
            wh_name = wh.name
        items: list[WriteoffItemRow] = []
        if load_items:
            item_rows = session.scalars(
                select(WarehouseWriteoffItem)
                .where(WarehouseWriteoffItem.writeoff_id == int(row.id))
                .order_by(WarehouseWriteoffItem.sort_order, WarehouseWriteoffItem.id)
            ).all()
            for it in item_rows:
                product = session.get(CatalogProduct, int(it.product_id))
                code = str(product.code) if product else ""
                items.append(
                    WriteoffItemRow(
                        id=int(it.id),
                        product_id=int(it.product_id),
                        sku=str(it.sku),
                        name=str(it.name),
                        code=code,
                        image_url=str(it.image_url or ""),
                        is_kit=bool(it.is_kit),
                        quantity=int(it.quantity),
                        unit_price=str(it.unit_price) if it.unit_price else None,
                        line_sum=str(it.line_sum) if it.line_sum else None,
                        sort_order=int(it.sort_order),
                    )
                )
        return WriteoffRow(
            id=int(row.id),
            title=str(row.title),
            display_name=_display_name(row.title),
            warehouse_id=int(row.warehouse_id),
            warehouse_name=wh_name,
            comment=str(row.comment or ""),
            total_quantity=int(row.total_quantity),
            total_sum=str(row.total_sum or "0.00"),
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
            items=items,
        )

    def _list_filter_conditions(self, filters: dict[str, str]) -> list:
        conds = []
        for key, col in (
            ("title", WarehouseWriteoff.title),
            ("comment", WarehouseWriteoff.comment),
        ):
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        raw_wh = (filters.get("warehouse_id") or "").strip()
        if raw_wh:
            try:
                conds.append(WarehouseWriteoff.warehouse_id == int(raw_wh))
            except ValueError:
                pass
        q_text = _like(filters.get("q", ""))
        if q_text:
            conds.append(
                or_(
                    WarehouseWriteoff.title.ilike(q_text),
                    WarehouseWriteoff.comment.ilike(q_text),
                )
            )
        return conds


def _calc_lines_total_sum(line_sums: list[str]) -> str:
    from decimal import Decimal, InvalidOperation

    total = Decimal("0")
    for raw in line_sums:
        val = str(raw or "").strip().replace(",", ".")
        if not val:
            continue
        try:
            total += Decimal(val)
        except InvalidOperation:
            continue
    return f"{total:.2f}"


def _calc_items_total_sum(items: list[dict[str, Any]]) -> str:
    return _calc_lines_total_sum([str(item.get("line_sum") or "") for item in items])


def _parse_price_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _parse_price(raw)


def _sum_from_price(unit_price: str, quantity: int) -> str:
    from decimal import Decimal

    amount = Decimal(unit_price) * Decimal(max(1, int(quantity)))
    return f"{amount:.2f}"


def _price_from_sum(line_sum: str, quantity: int) -> str:
    from decimal import Decimal, ROUND_HALF_UP

    qty = max(1, int(quantity))
    amount = Decimal(line_sum) / Decimal(qty)
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"
