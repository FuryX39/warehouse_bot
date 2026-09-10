"""Инвентаризация: пересчёт ячейки → списание / оприходование."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogProduct
from app.storage_warehouse_repository import StorageBin, StorageWarehouse, StorageWarehouseRepository
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository

INV_DRAFT = "draft"
INV_POSTED = "posted"


class _Base(DeclarativeBase):
    pass


class WarehouseInventoryCount(_Base):
    __tablename__ = "warehouse_inventory_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=INV_DRAFT)
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    writeoff_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posted_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseInventoryCountLine(_Base):
    __tablename__ = "warehouse_inventory_count_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_inventory_counts.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    fact_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class InventoryCountRow:
    id: int
    number: str
    warehouse_id: int
    warehouse_name: str
    bin_id: int
    bin_name: str
    status: str
    comment: str
    writeoff_id: int | None
    receipt_id: int | None
    created_at_ts: int
    posted_at_ts: int
    lines: list[dict[str, Any]] = field(default_factory=list)


class WarehouseInventoryCountsRepository:
    def __init__(
        self,
        db_url: str,
        storage_repo: StorageWarehouseRepository,
        receipts_repo: WarehouseReceiptsRepository,
        writeoffs_repo: WarehouseWriteoffsRepository,
    ) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.storage_repo = storage_repo
        self.receipts_repo = receipts_repo
        self.writeoffs_repo = writeoffs_repo

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def _next_number(self, session: Session) -> str:
        max_n = 0
        for num in session.scalars(select(WarehouseInventoryCount.number)).all():
            text = str(num or "")
            if text.startswith("ИН-") and text[3:].isdigit():
                max_n = max(max_n, int(text[3:]))
        return f"ИН-{max_n + 1:05d}"

    def list_counts(self) -> list[InventoryCountRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseInventoryCount).order_by(
                    WarehouseInventoryCount.created_at_ts.desc(), WarehouseInventoryCount.id.desc()
                )
            ).all()
            return [self._row(session, r, load_lines=False) for r in rows]

    def get_count(self, count_id: int) -> InventoryCountRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseInventoryCount, int(count_id))
            if row is None:
                return None
            return self._row(session, row, load_lines=True)

    def fill_from_bin(self, warehouse_id: int, bin_id: int) -> list[dict[str, Any]]:
        stocks = self.storage_repo.list_stocks_for_bin(int(warehouse_id), int(bin_id))
        with Session(self.engine) as session:
            catalog = {
                str(p.sku).strip().casefold(): p
                for p in session.scalars(select(CatalogProduct)).all()
                if str(p.sku or "").strip()
            }
        out = []
        for sku, qty in sorted(stocks.items()):
            product = catalog.get(sku.casefold())
            out.append(
                {
                    "sku": sku,
                    "product_id": int(product.id) if product else None,
                    "name": str(product.name) if product else sku,
                    "book_qty": int(qty),
                    "fact_qty": int(qty),
                }
            )
        return out

    def save_draft(self, data: dict[str, Any], count_id: int | None = None) -> InventoryCountRow:
        warehouse_id = int(data.get("warehouse_id"))
        bin_id = self.storage_repo.resolve_bin_id(warehouse_id, data.get("bin_id"))
        comment = str(data.get("comment") or "").strip()[:2048]
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Добавьте хотя бы одну строку")
        now = int(time.time())
        with Session(self.engine) as session:
            if count_id:
                row = session.get(WarehouseInventoryCount, int(count_id))
                if row is None:
                    raise ValueError("Пересчёт не найден")
                if row.status == INV_POSTED:
                    raise ValueError("Проведённый пересчёт нельзя менять")
                row.warehouse_id = warehouse_id
                row.bin_id = bin_id
                row.comment = comment
                session.execute(
                    delete(WarehouseInventoryCountLine).where(
                        WarehouseInventoryCountLine.count_id == int(row.id)
                    )
                )
            else:
                row = WarehouseInventoryCount(
                    number=self._next_number(session),
                    warehouse_id=warehouse_id,
                    bin_id=bin_id,
                    status=INV_DRAFT,
                    comment=comment,
                    created_at_ts=now,
                )
                session.add(row)
                session.flush()
            for i, raw in enumerate(items):
                sku = str(raw.get("sku") or "").strip()
                if not sku:
                    continue
                session.add(
                    WarehouseInventoryCountLine(
                        count_id=int(row.id),
                        sku=sku,
                        product_id=int(raw["product_id"]) if raw.get("product_id") else None,
                        name=str(raw.get("name") or sku)[:512],
                        fact_qty=int(raw.get("fact_qty") or 0),
                    )
                )
            session.commit()
            session.refresh(row)
            return self._row(session, row, load_lines=True)

    def post_count(self, count_id: int) -> InventoryCountRow:
        with Session(self.engine) as session:
            row = session.get(WarehouseInventoryCount, int(count_id))
            if row is None:
                raise ValueError("Пересчёт не найден")
            if row.status == INV_POSTED:
                raise ValueError("Пересчёт уже проведён")
            lines = list(
                session.scalars(
                    select(WarehouseInventoryCountLine).where(
                        WarehouseInventoryCountLine.count_id == int(row.id)
                    )
                ).all()
            )
            warehouse_id = int(row.warehouse_id)
            bin_id = int(row.bin_id)
            number = str(row.number)
        shortage: list[dict[str, Any]] = []
        surplus: list[dict[str, Any]] = []
        with Session(self.engine) as session:
            catalog = {
                int(p.id): p
                for p in session.scalars(select(CatalogProduct)).all()
            }
            by_sku = {
                str(p.sku or "").strip().casefold(): p
                for p in catalog.values()
                if str(p.sku or "").strip()
            }
        for ln in lines:
            book = self.storage_repo.get_stock(warehouse_id, str(ln.sku), bin_id=bin_id)
            fact = int(ln.fact_qty)
            delta = fact - book
            if delta == 0:
                continue
            product = None
            if ln.product_id:
                product = catalog.get(int(ln.product_id))
            if product is None:
                product = by_sku.get(str(ln.sku).casefold())
            if product is None:
                raise ValueError(f"Артикул «{ln.sku}» нет в каталоге — нельзя создать документ")
            payload = {
                "product_id": int(product.id),
                "quantity": abs(int(delta)),
            }
            if delta < 0:
                shortage.append(payload)
            else:
                surplus.append(payload)
        writeoff_id = None
        receipt_id = None
        if shortage:
            wo = self.writeoffs_repo.create_writeoff(
                {
                    "title": f"{number} недостача",
                    "warehouse_id": warehouse_id,
                    "bin_id": bin_id,
                    "comment": f"На основании инвентаризации {number}",
                    "items": shortage,
                    "locked": True,
                }
            )
            writeoff_id = int(wo.id)
        if surplus:
            rc = self.receipts_repo.create_receipt(
                {
                    "title": f"{number} излишек",
                    "warehouse_id": warehouse_id,
                    "bin_id": bin_id,
                    "comment": f"На основании инвентаризации {number}",
                    "items": surplus,
                    "locked": True,
                }
            )
            receipt_id = int(rc.id)
        with Session(self.engine) as session:
            row = session.get(WarehouseInventoryCount, int(count_id))
            row.status = INV_POSTED
            row.posted_at_ts = int(time.time())
            row.writeoff_id = writeoff_id
            row.receipt_id = receipt_id
            session.commit()
            session.refresh(row)
            return self._row(session, row, load_lines=True)

    def to_dict(self, row: InventoryCountRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "number": row.number,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "bin_id": row.bin_id,
            "bin_name": row.bin_name,
            "status": row.status,
            "comment": row.comment,
            "writeoff_id": row.writeoff_id,
            "receipt_id": row.receipt_id,
            "created_at_ts": row.created_at_ts,
            "posted_at_ts": row.posted_at_ts,
            "lines": row.lines,
        }

    def _row(self, session: Session, row: WarehouseInventoryCount, *, load_lines: bool) -> InventoryCountRow:
        wh = session.get(StorageWarehouse, int(row.warehouse_id))
        bn = session.get(StorageBin, int(row.bin_id))
        lines: list[dict[str, Any]] = []
        if load_lines:
            for ln in session.scalars(
                select(WarehouseInventoryCountLine).where(
                    WarehouseInventoryCountLine.count_id == int(row.id)
                )
            ).all():
                book = self.storage_repo.get_stock(
                    int(row.warehouse_id), str(ln.sku), bin_id=int(row.bin_id)
                )
                lines.append(
                    {
                        "sku": str(ln.sku),
                        "product_id": int(ln.product_id) if ln.product_id else None,
                        "name": str(ln.name),
                        "book_qty": book,
                        "fact_qty": int(ln.fact_qty),
                        "delta": int(ln.fact_qty) - book,
                    }
                )
        return InventoryCountRow(
            id=int(row.id),
            number=str(row.number),
            warehouse_id=int(row.warehouse_id),
            warehouse_name=wh.name if wh else "",
            bin_id=int(row.bin_id),
            bin_name=(bn.name or bn.code) if bn else "",
            status=str(row.status),
            comment=str(row.comment or ""),
            writeoff_id=int(row.writeoff_id) if row.writeoff_id else None,
            receipt_id=int(row.receipt_id) if row.receipt_id else None,
            created_at_ts=int(row.created_at_ts),
            posted_at_ts=int(row.posted_at_ts),
            lines=lines,
        )
