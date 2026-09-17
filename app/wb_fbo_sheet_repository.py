"""Задания FBO WB new: раскладка по таблицам кабинета, без записи в WB API."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_task_files import MAX_TASK_ATTACHMENT_BYTES, WarehouseTaskFileStorage

JOB_STATUS_OPEN = "open"
JOB_STATUS_IN_PROGRESS = "in_progress"
JOB_STATUS_DONE = "done"
JOB_STATUS_CANCELLED = "cancelled"
JOB_ACTIVE_STATUSES = (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS)

BOX_PENDING = "pending"
BOX_PRINTED = "printed"
BOX_ASSIGNED = "assigned"


class _Base(DeclarativeBase):
    pass


class WbFboSheetJob(_Base):
    __tablename__ = "wb_fbo_sheet_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    seller_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    plan_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    box_type: Mapped[str] = mapped_column(String(64), nullable=False, default="Короб")
    goods_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    boxes_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JOB_STATUS_OPEN)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WbFboSheetJobAssignee(_Base):
    __tablename__ = "wb_fbo_sheet_job_assignees"

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_sheet_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class WbFboSheetProduct(_Base):
    __tablename__ = "wb_fbo_sheet_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_sheet_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    barcode: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sku: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_plan: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WbFboSheetBox(_Base):
    __tablename__ = "wb_fbo_sheet_boxes"
    __table_args__ = (
        UniqueConstraint("job_id", "box_human_id", name="uq_wb_fbo_sheet_box_human"),
        UniqueConstraint("job_id", "package_code", name="uq_wb_fbo_sheet_box_pkg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_sheet_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    box_human_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    package_code: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_barcode: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    item_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=BOX_PENDING)
    printed_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WbFboSheetBoxItem(_Base):
    __tablename__ = "wb_fbo_sheet_box_items"
    __table_args__ = (
        UniqueConstraint("box_id", "product_barcode", name="uq_wb_fbo_sheet_box_item_barcode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_sheet_jobs.id", ondelete="CASCADE"), nullable=False
    )
    box_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_sheet_boxes.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_barcode: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    item_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _parse_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item or "").strip()]


@dataclass
class WbFboSheetProductRow:
    id: int
    job_id: int
    seq: int
    barcode: str
    sku: str
    name: str
    product_id: int | None
    qty_plan: int
    qty_assigned: int = 0


@dataclass
class WbFboSheetBoxItemRow:
    id: int
    product_barcode: str
    item_qty: int
    sku: str = ""
    product_name: str = ""
    product_id: int | None = None


@dataclass
class WbFboSheetBoxRow:
    id: int
    job_id: int
    seq: int
    box_human_id: str
    package_code: str
    product_barcode: str
    item_qty: int
    status: str
    printed_at_ts: int | None
    assigned_at_ts: int | None
    assigned_by_user_id: int | None
    sku: str = ""
    product_name: str = ""
    product_id: int | None = None
    items: list[WbFboSheetBoxItemRow] = field(default_factory=list)


@dataclass
class WbFboSheetJobRow:
    id: int
    supply_id: str
    warehouse_name: str
    seller_name: str
    plan_date: str
    box_type: str
    goods_stored_name: str
    boxes_stored_name: str
    status: str
    created_by_user_id: int | None
    warnings: list[str]
    created_at_ts: int
    updated_at_ts: int
    packer_user_ids: list[int]
    packer_names: list[str]
    product_total: int
    pcs_plan: int
    pcs_assigned: int
    box_total: int
    box_printed: int
    box_assigned: int
    box_pending: int
    products: list[WbFboSheetProductRow] = field(default_factory=list)
    boxes: list[WbFboSheetBoxRow] = field(default_factory=list)


class WbFboSheetRepository:
    def __init__(self, db_url: str, *, files_data_dir: str | Path) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.file_storage = WarehouseTaskFileStorage(Path(files_data_dir))

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._backfill_box_items()

    def _backfill_box_items(self) -> None:
        with Session(self.engine) as session:
            existing = {int(x) for x in session.scalars(select(WbFboSheetBoxItem.box_id)).all()}
            boxes = session.scalars(select(WbFboSheetBox)).all()
            added = False
            for box in boxes:
                if int(box.id) in existing:
                    continue
                barcode = str(box.product_barcode or "").strip()
                qty = int(box.item_qty or 0)
                if not barcode or qty <= 0:
                    continue
                session.add(
                    WbFboSheetBoxItem(
                        job_id=int(box.job_id),
                        box_id=int(box.id),
                        seq=1,
                        product_barcode=barcode,
                        item_qty=qty,
                        assigned_at_ts=box.assigned_at_ts,
                        assigned_by_user_id=box.assigned_by_user_id,
                    )
                )
                added = True
            if added:
                session.commit()

    def store_xlsx(self, content: bytes, *, original_filename: str = "file.xlsx") -> str:
        if not content:
            raise ValueError("Файл пустой")
        if len(content) > MAX_TASK_ATTACHMENT_BYTES:
            raise ValueError("Файл слишком большой (макс. 20 МБ)")
        name = str(original_filename or "").lower()
        if not content.startswith(b"PK") and not name.endswith(".xlsx"):
            raise ValueError("Нужен файл Excel (.xlsx)")
        stored_name = f"{uuid.uuid4().hex}.xlsx"
        path = self.file_storage.data_dir / stored_name
        path.write_bytes(content)
        return stored_name

    def read_stored(self, stored_name: str) -> bytes:
        path = self.file_storage.path_for(stored_name)
        if path is None:
            raise ValueError("Файл не найден")
        return path.read_bytes()

    def _product_row(
        self, row: WbFboSheetProduct, *, qty_assigned: int = 0
    ) -> WbFboSheetProductRow:
        return WbFboSheetProductRow(
            id=int(row.id),
            job_id=int(row.job_id),
            seq=int(row.seq),
            barcode=str(row.barcode or ""),
            sku=str(row.sku or ""),
            name=str(row.name or ""),
            product_id=int(row.product_id) if row.product_id else None,
            qty_plan=int(row.qty_plan or 0),
            qty_assigned=int(qty_assigned),
        )

    def _items_for_box(self, session: Session, box_id: int) -> list[WbFboSheetBoxItem]:
        return list(
            session.scalars(
                select(WbFboSheetBoxItem)
                .where(WbFboSheetBoxItem.box_id == int(box_id))
                .order_by(WbFboSheetBoxItem.seq, WbFboSheetBoxItem.id)
            ).all()
        )

    def _sku_by_barcode(self, session: Session, job_id: int) -> dict[str, WbFboSheetProductRow]:
        products = session.scalars(
            select(WbFboSheetProduct).where(WbFboSheetProduct.job_id == int(job_id))
        ).all()
        return {
            str(item.barcode or "").casefold(): self._product_row(item) for item in products
        }

    def _box_item_row(
        self,
        row: WbFboSheetBoxItem,
        *,
        sku_by_barcode: dict[str, WbFboSheetProductRow] | None = None,
    ) -> WbFboSheetBoxItemRow:
        barcode = str(row.product_barcode or "")
        product = (sku_by_barcode or {}).get(barcode.casefold())
        return WbFboSheetBoxItemRow(
            id=int(row.id),
            product_barcode=barcode,
            item_qty=int(row.item_qty or 0),
            sku=product.sku if product else "",
            product_name=product.name if product else "",
            product_id=product.product_id if product else None,
        )

    def _box_row(
        self,
        row: WbFboSheetBox,
        *,
        items: list[WbFboSheetBoxItem] | None = None,
        sku_by_barcode: dict[str, WbFboSheetProductRow] | None = None,
    ) -> WbFboSheetBoxRow:
        src_items = list(items or [])
        item_rows = [
            self._box_item_row(item, sku_by_barcode=sku_by_barcode) for item in src_items
        ]
        if not item_rows:
            barcode = str(row.product_barcode or "").strip()
            qty = int(row.item_qty or 0)
            if barcode and qty > 0:
                product = (sku_by_barcode or {}).get(barcode.casefold())
                item_rows = [
                    WbFboSheetBoxItemRow(
                        id=0,
                        product_barcode=barcode,
                        item_qty=qty,
                        sku=product.sku if product else "",
                        product_name=product.name if product else "",
                        product_id=product.product_id if product else None,
                    )
                ]
        barcodes = [item.product_barcode for item in item_rows if item.product_barcode]
        skus = [item.sku for item in item_rows if item.sku]
        names = [item.product_name for item in item_rows if item.product_name]
        pids = [item.product_id for item in item_rows if item.product_id]
        total_qty = sum(item.item_qty for item in item_rows)
        return WbFboSheetBoxRow(
            id=int(row.id),
            job_id=int(row.job_id),
            seq=int(row.seq),
            box_human_id=str(row.box_human_id or ""),
            package_code=str(row.package_code or ""),
            product_barcode=barcodes[0] if len(barcodes) == 1 else ",".join(barcodes),
            item_qty=total_qty if item_rows else int(row.item_qty or 0),
            status=str(row.status or BOX_PENDING),
            printed_at_ts=int(row.printed_at_ts) if row.printed_at_ts else None,
            assigned_at_ts=int(row.assigned_at_ts) if row.assigned_at_ts else None,
            assigned_by_user_id=int(row.assigned_by_user_id) if row.assigned_by_user_id else None,
            sku=", ".join(skus),
            product_name=", ".join(names),
            product_id=pids[0] if len(pids) == 1 else None,
            items=item_rows,
        )

    def _assigned_qty_by_barcode(self, session: Session, job_id: int) -> dict[str, int]:
        rows = session.scalars(
            select(WbFboSheetBoxItem).where(WbFboSheetBoxItem.job_id == int(job_id))
        ).all()
        out: dict[str, int] = {}
        for row in rows:
            key = str(row.product_barcode or "").strip().casefold()
            if not key:
                continue
            out[key] = out.get(key, 0) + int(row.item_qty or 0)
        return out

    def _job_row(
        self,
        session: Session,
        job: WbFboSheetJob,
        *,
        include_lines: bool = False,
        packer_names: dict[int, str] | None = None,
    ) -> WbFboSheetJobRow:
        assignees = session.scalars(
            select(WbFboSheetJobAssignee.user_id).where(
                WbFboSheetJobAssignee.job_id == int(job.id)
            )
        ).all()
        packer_ids = [int(x) for x in assignees]
        names = []
        if packer_names:
            names = [packer_names.get(uid, str(uid)) for uid in packer_ids]
        products = list(
            session.scalars(
                select(WbFboSheetProduct)
                .where(WbFboSheetProduct.job_id == int(job.id))
                .order_by(WbFboSheetProduct.seq, WbFboSheetProduct.id)
            ).all()
        )
        boxes = list(
            session.scalars(
                select(WbFboSheetBox)
                .where(WbFboSheetBox.job_id == int(job.id))
                .order_by(WbFboSheetBox.seq, WbFboSheetBox.id)
            ).all()
        )
        assigned_map = self._assigned_qty_by_barcode(session, int(job.id))
        product_rows = [
            self._product_row(
                row, qty_assigned=assigned_map.get(str(row.barcode or "").casefold(), 0)
            )
            for row in products
        ]
        sku_by_barcode = {
            str(item.barcode or "").casefold(): item for item in product_rows
        }
        item_orm = list(
            session.scalars(
                select(WbFboSheetBoxItem)
                .where(WbFboSheetBoxItem.job_id == int(job.id))
                .order_by(WbFboSheetBoxItem.seq, WbFboSheetBoxItem.id)
            ).all()
        )
        items_by_box: dict[int, list[WbFboSheetBoxItem]] = {}
        for item in item_orm:
            items_by_box.setdefault(int(item.box_id), []).append(item)
        box_rows = [
            self._box_row(
                row,
                items=items_by_box.get(int(row.id), []),
                sku_by_barcode=sku_by_barcode,
            )
            for row in boxes
        ]
        return WbFboSheetJobRow(
            id=int(job.id),
            supply_id=str(job.supply_id or ""),
            warehouse_name=str(job.warehouse_name or ""),
            seller_name=str(job.seller_name or ""),
            plan_date=str(job.plan_date or ""),
            box_type=str(job.box_type or "Короб"),
            goods_stored_name=str(job.goods_stored_name or ""),
            boxes_stored_name=str(job.boxes_stored_name or ""),
            status=str(job.status or ""),
            created_by_user_id=int(job.created_by_user_id) if job.created_by_user_id else None,
            warnings=_parse_json_list(job.warnings_json),
            created_at_ts=int(job.created_at_ts or 0),
            updated_at_ts=int(job.updated_at_ts or 0),
            packer_user_ids=packer_ids,
            packer_names=names,
            product_total=len(product_rows),
            pcs_plan=sum(item.qty_plan for item in product_rows),
            pcs_assigned=sum(item.qty_assigned for item in product_rows),
            box_total=len(box_rows),
            box_printed=sum(1 for item in box_rows if item.printed_at_ts),
            box_assigned=sum(1 for item in box_rows if item.status == BOX_ASSIGNED),
            box_pending=sum(1 for item in box_rows if item.status == BOX_PENDING),
            products=product_rows if include_lines else [],
            boxes=box_rows if include_lines else [],
        )

    def create_job(
        self,
        *,
        supply_id: str,
        warehouse_name: str,
        seller_name: str,
        plan_date: str,
        box_type: str,
        packer_user_ids: list[int],
        created_by_user_id: int | None,
        warnings: list[str],
        goods_xlsx: bytes,
        boxes_xlsx: bytes,
        products: list[dict[str, Any]],
        boxes: list[dict[str, Any]],
    ) -> WbFboSheetJobRow:
        now = int(time.time())
        goods_name = self.store_xlsx(goods_xlsx, original_filename="goods.xlsx")
        boxes_name = self.store_xlsx(boxes_xlsx, original_filename="boxes.xlsx")
        with Session(self.engine) as session:
            job = WbFboSheetJob(
                supply_id=str(supply_id or ""),
                warehouse_name=str(warehouse_name or ""),
                seller_name=str(seller_name or ""),
                plan_date=str(plan_date or ""),
                box_type=str(box_type or "Короб") or "Короб",
                goods_stored_name=goods_name,
                boxes_stored_name=boxes_name,
                status=JOB_STATUS_OPEN,
                created_by_user_id=created_by_user_id,
                warnings_json=json.dumps(list(warnings or []), ensure_ascii=False),
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(job)
            session.flush()
            for uid in packer_user_ids:
                session.add(WbFboSheetJobAssignee(job_id=int(job.id), user_id=int(uid)))
            for seq, item in enumerate(products, start=1):
                session.add(
                    WbFboSheetProduct(
                        job_id=int(job.id),
                        seq=seq,
                        barcode=str(item.get("barcode") or ""),
                        sku=str(item.get("sku") or ""),
                        name=str(item.get("name") or ""),
                        product_id=int(item["product_id"]) if item.get("product_id") else None,
                        qty_plan=int(item.get("qty_plan") or 0),
                    )
                )
            assigned_any = False
            for seq, item in enumerate(boxes, start=1):
                lines = [
                    line
                    for line in list(item.get("items") or [])
                    if isinstance(line, dict)
                    and str(line.get("product_barcode") or "").strip()
                    and int(line.get("qty") or 0) > 0
                ]
                if not lines:
                    product_barcode = str(item.get("product_barcode") or "").strip()
                    qty = int(item.get("qty") or 0)
                    if product_barcode and qty > 0:
                        lines = [{"product_barcode": product_barcode, "qty": qty}]
                status = BOX_ASSIGNED if lines else BOX_PENDING
                assigned_any = assigned_any or status == BOX_ASSIGNED
                box = WbFboSheetBox(
                    job_id=int(job.id),
                    seq=seq,
                    box_human_id=str(item.get("box_id") or ""),
                    package_code=str(item.get("package_code") or ""),
                    product_barcode=str(lines[0]["product_barcode"]) if len(lines) == 1 else "",
                    item_qty=sum(int(line.get("qty") or 0) for line in lines) if status == BOX_ASSIGNED else 0,
                    status=status,
                    assigned_at_ts=now if status == BOX_ASSIGNED else None,
                )
                session.add(box)
                session.flush()
                for item_seq, line in enumerate(lines, start=1):
                    session.add(
                        WbFboSheetBoxItem(
                            job_id=int(job.id),
                            box_id=int(box.id),
                            seq=item_seq,
                            product_barcode=str(line.get("product_barcode") or "").strip(),
                            item_qty=int(line.get("qty") or 0),
                            assigned_at_ts=now if status == BOX_ASSIGNED else None,
                        )
                    )
            if assigned_any:
                job.status = JOB_STATUS_IN_PROGRESS
            session.commit()
            session.refresh(job)
            return self._job_row(session, job, include_lines=True)

    def list_jobs(self, *, packer_names: dict[int, str] | None = None) -> list[WbFboSheetJobRow]:
        with Session(self.engine) as session:
            rows = session.scalars(select(WbFboSheetJob).order_by(WbFboSheetJob.id.desc())).all()
            return [self._job_row(session, row, packer_names=packer_names) for row in rows]

    def list_my_jobs(self, user_id: int) -> list[WbFboSheetJobRow]:
        with Session(self.engine) as session:
            job_ids = session.scalars(
                select(WbFboSheetJobAssignee.job_id).where(
                    WbFboSheetJobAssignee.user_id == int(user_id)
                )
            ).all()
            if not job_ids:
                return []
            rows = session.scalars(
                select(WbFboSheetJob)
                .where(
                    WbFboSheetJob.id.in_([int(x) for x in job_ids]),
                    WbFboSheetJob.status.in_(JOB_ACTIVE_STATUSES),
                )
                .order_by(WbFboSheetJob.id.desc())
            ).all()
            return [self._job_row(session, row) for row in rows]

    def get_job(self, job_id: int, *, include_lines: bool = False) -> WbFboSheetJobRow | None:
        with Session(self.engine) as session:
            job = session.get(WbFboSheetJob, int(job_id))
            if job is None:
                return None
            return self._job_row(session, job, include_lines=include_lines)

    def user_can_pack(self, job_id: int, user_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WbFboSheetJobAssignee, (int(job_id), int(user_id)))
            return row is not None

    def cancel_job(self, job_id: int) -> WbFboSheetJobRow | None:
        with Session(self.engine) as session:
            job = session.get(WbFboSheetJob, int(job_id))
            if job is None:
                return None
            job.status = JOB_STATUS_CANCELLED
            job.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(job)
            return self._job_row(session, job)

    def _require_active(self, session: Session, job_id: int) -> WbFboSheetJob:
        job = session.get(WbFboSheetJob, int(job_id))
        if job is None:
            raise ValueError("Задание не найдено")
        if job.status == JOB_STATUS_CANCELLED:
            raise ValueError("Задание отменено")
        if job.status == JOB_STATUS_DONE:
            raise ValueError("Задание уже выполнено")
        return job

    def mark_boxes_printed(self, job_id: int, box_ids: list[int]) -> list[WbFboSheetBoxRow]:
        now = int(time.time())
        ids = [int(x) for x in box_ids if int(x) > 0]
        if not ids:
            raise ValueError("Нет грузомест для печати")
        with Session(self.engine) as session:
            job = self._require_active(session, job_id)
            rows = list(
                session.scalars(
                    select(WbFboSheetBox)
                    .where(
                        WbFboSheetBox.job_id == int(job_id),
                        WbFboSheetBox.id.in_(ids),
                    )
                    .order_by(WbFboSheetBox.seq, WbFboSheetBox.id)
                ).all()
            )
            if len(rows) != len(ids):
                raise ValueError("Грузоместо не найдено в задании")
            for row in rows:
                if row.status == BOX_ASSIGNED:
                    raise ValueError("Нельзя печатать уже присвоенное грузоместо этой кнопкой")
                row.printed_at_ts = now
                if row.status == BOX_PENDING:
                    row.status = BOX_PRINTED
            if job.status == JOB_STATUS_OPEN:
                job.status = JOB_STATUS_IN_PROGRESS
            job.updated_at_ts = now
            session.commit()
            sku_by = self._sku_by_barcode(session, job_id)
            return [
                self._box_row(row, items=self._items_for_box(session, int(row.id)), sku_by_barcode=sku_by)
                for row in rows
            ]

    def next_unprinted_boxes(self, job_id: int, count: int) -> list[WbFboSheetBoxRow]:
        want = int(count)
        if want <= 0:
            raise ValueError("Укажите количество ШК для печати")
        with Session(self.engine) as session:
            self._require_active(session, job_id)
            rows = list(
                session.scalars(
                    select(WbFboSheetBox)
                    .where(
                        WbFboSheetBox.job_id == int(job_id),
                        WbFboSheetBox.printed_at_ts.is_(None),
                        WbFboSheetBox.status == BOX_PENDING,
                    )
                    .order_by(WbFboSheetBox.seq, WbFboSheetBox.id)
                    .limit(want)
                ).all()
            )
            if not rows:
                raise ValueError("Нет ещё не печатавшихся грузомест")
            sku_by = self._sku_by_barcode(session, job_id)
            return [
                self._box_row(row, items=self._items_for_box(session, int(row.id)), sku_by_barcode=sku_by)
                for row in rows
            ]

    def get_box(self, job_id: int, box_id: int) -> WbFboSheetBoxRow | None:
        with Session(self.engine) as session:
            row = session.get(WbFboSheetBox, int(box_id))
            if row is None or int(row.job_id) != int(job_id):
                return None
            sku_by = self._sku_by_barcode(session, job_id)
            return self._box_row(
                row,
                items=self._items_for_box(session, int(row.id)),
                sku_by_barcode=sku_by,
            )

    def remaining_pcs(self, job_id: int, product_barcode: str) -> int:
        key = str(product_barcode or "").strip().casefold()
        with Session(self.engine) as session:
            product = session.scalars(
                select(WbFboSheetProduct).where(
                    WbFboSheetProduct.job_id == int(job_id),
                    WbFboSheetProduct.barcode == str(product_barcode or "").strip(),
                )
            ).first()
            if product is None:
                product = session.scalars(
                    select(WbFboSheetProduct).where(WbFboSheetProduct.job_id == int(job_id))
                ).first()
                for row in session.scalars(
                    select(WbFboSheetProduct).where(WbFboSheetProduct.job_id == int(job_id))
                ).all():
                    if str(row.barcode or "").casefold() == key:
                        product = row
                        break
                else:
                    return 0
            assigned = self._assigned_qty_by_barcode(session, job_id).get(key, 0)
            return max(0, int(product.qty_plan or 0) - assigned)

    def assign_box(
        self,
        job_id: int,
        user_id: int,
        *,
        box_id: int,
        product_barcode: str,
        item_qty: int,
    ) -> WbFboSheetBoxRow:
        now = int(time.time())
        qty = int(item_qty)
        barcode = str(product_barcode or "").strip()
        if qty <= 0:
            raise ValueError("Количество должно быть больше 0")
        if not barcode:
            raise ValueError("Нет баркода товара")
        with Session(self.engine) as session:
            job = self._require_active(session, job_id)
            box = session.get(WbFboSheetBox, int(box_id))
            if box is None or int(box.job_id) != int(job_id):
                raise ValueError("Грузоместо не найдено")
            product = None
            for row in session.scalars(
                select(WbFboSheetProduct).where(WbFboSheetProduct.job_id == int(job_id))
            ).all():
                if str(row.barcode or "").strip().casefold() == barcode.casefold():
                    product = row
                    break
            if product is None:
                raise ValueError("Товара с этим баркодом нет в задании")
            assigned = self._assigned_qty_by_barcode(session, job_id).get(barcode.casefold(), 0)
            left = int(product.qty_plan or 0) - assigned
            if qty > left:
                raise ValueError(f"Осталось {left} шт. этого товара, нельзя положить {qty}")
            items = self._items_for_box(session, int(box.id))
            existing = next(
                (
                    item
                    for item in items
                    if str(item.product_barcode or "").casefold() == barcode.casefold()
                ),
                None,
            )
            if existing is not None:
                existing.item_qty = int(existing.item_qty or 0) + qty
                existing.assigned_at_ts = now
                existing.assigned_by_user_id = int(user_id)
            else:
                next_seq = int(
                    session.scalar(
                        select(func.coalesce(func.max(WbFboSheetBoxItem.seq), 0)).where(
                            WbFboSheetBoxItem.box_id == int(box.id)
                        )
                    )
                    or 0
                )
                session.add(
                    WbFboSheetBoxItem(
                        job_id=int(job_id),
                        box_id=int(box.id),
                        seq=next_seq + 1,
                        product_barcode=str(product.barcode),
                        item_qty=qty,
                        assigned_at_ts=now,
                        assigned_by_user_id=int(user_id),
                    )
                )
            session.flush()
            items = self._items_for_box(session, int(box.id))
            box.status = BOX_ASSIGNED
            box.assigned_at_ts = now
            box.assigned_by_user_id = int(user_id)
            box.item_qty = sum(int(item.item_qty or 0) for item in items)
            box.product_barcode = (
                str(items[0].product_barcode) if len(items) == 1 else ""
            )
            if job.status == JOB_STATUS_OPEN:
                job.status = JOB_STATUS_IN_PROGRESS
            job.updated_at_ts = now
            qty_map = self._assigned_qty_by_barcode(session, job_id)
            remaining_products = session.scalars(
                select(WbFboSheetProduct).where(WbFboSheetProduct.job_id == int(job_id))
            ).all()
            if remaining_products and all(
                qty_map.get(str(item.barcode or "").casefold(), 0) >= int(item.qty_plan or 0)
                for item in remaining_products
            ):
                job.status = JOB_STATUS_DONE
            session.commit()
            session.refresh(box)
            sku_by = self._sku_by_barcode(session, job_id)
            return self._box_row(
                box,
                items=self._items_for_box(session, int(box.id)),
                sku_by_barcode=sku_by,
            )

    def product_to_dict(self, row: WbFboSheetProductRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "job_id": row.job_id,
            "seq": row.seq,
            "barcode": row.barcode,
            "sku": row.sku,
            "name": row.name,
            "product_id": row.product_id,
            "qty_plan": row.qty_plan,
            "qty_assigned": row.qty_assigned,
            "quantity": max(0, row.qty_plan - row.qty_assigned),
        }

    def box_to_dict(self, row: WbFboSheetBoxRow) -> dict[str, Any]:
        items = [
            {
                "product_barcode": item.product_barcode,
                "sku": item.sku,
                "product_name": item.product_name,
                "product_id": item.product_id,
                "quantity": item.item_qty,
                "item_qty": item.item_qty,
            }
            for item in row.items
        ]
        return {
            "id": row.id,
            "job_id": row.job_id,
            "seq": row.seq,
            "box_id": row.box_human_id,
            "order_display": row.box_human_id,
            "package_code": row.package_code,
            "product_barcode": row.product_barcode,
            "sku": row.sku,
            "product_name": row.product_name,
            "product_id": row.product_id,
            "quantity": row.item_qty,
            "status": row.status,
            "items": items,
        }

    def box_assignment_rows(self, job: WbFboSheetJobRow) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for box in job.boxes:
            if box.status != BOX_ASSIGNED:
                continue
            if box.items:
                for item in box.items:
                    rows.append(
                        {
                            "box_id": box.box_human_id,
                            "product_barcode": item.product_barcode,
                            "item_qty": item.item_qty,
                        }
                    )
                continue
            if box.product_barcode:
                rows.append(
                    {
                        "box_id": box.box_human_id,
                        "product_barcode": box.product_barcode,
                        "item_qty": box.item_qty,
                    }
                )
        return rows

    def job_to_dict(self, job: WbFboSheetJobRow, *, include_lines: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": job.id,
            "marketplace": "wildberries",
            "supply_id": job.supply_id,
            "warehouse_name": job.warehouse_name,
            "seller_name": job.seller_name,
            "plan_date": job.plan_date,
            "box_type": job.box_type,
            "status": job.status,
            "created_by_user_id": job.created_by_user_id,
            "warnings": job.warnings,
            "created_at_ts": job.created_at_ts,
            "updated_at_ts": job.updated_at_ts,
            "packer_user_ids": job.packer_user_ids,
            "packer_names": job.packer_names,
            "product_total": job.product_total,
            "pcs_plan": job.pcs_plan,
            "pcs_assigned": job.pcs_assigned,
            "box_total": job.box_total,
            "box_printed": job.box_printed,
            "box_assigned": job.box_assigned,
            "box_pending": job.box_pending,
            "line_total": job.box_total,
            "line_done": job.box_assigned,
            "line_printed": job.box_printed,
            "line_pending": job.box_pending,
        }
        if include_lines:
            payload["products"] = [self.product_to_dict(item) for item in job.products]
            payload["boxes"] = [self.box_to_dict(item) for item in job.boxes]
            payload["remaining_groups"] = [
                self.product_to_dict(item)
                for item in job.products
                if item.qty_plan - item.qty_assigned > 0
            ]
        return payload
