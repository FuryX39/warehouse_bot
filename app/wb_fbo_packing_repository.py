"""Задания FBO-упаковки Wildberries (FBW): короба, QR поставки, листы паллет."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_task_files import WarehouseTaskFileStorage

JOB_STATUS_OPEN = "open"
JOB_STATUS_IN_PROGRESS = "in_progress"
JOB_STATUS_DONE = "done"
JOB_STATUS_CANCELLED = "cancelled"
JOB_ACTIVE_STATUSES = (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS)

LINE_PENDING = "pending"
LINE_PRINTED = "printed"
LINE_DONE = "done"


class _Base(DeclarativeBase):
    pass


class WbFboPackingJob(_Base):
    __tablename__ = "wb_fbo_packing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    seller_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    plan_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    pallet_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supply_qr_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    supply_qr_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    pallet_sheets_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    box_labels_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JOB_STATUS_OPEN)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WbFboPackingJobAssignee(_Base):
    __tablename__ = "wb_fbo_packing_job_assignees"

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_packing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class WbFboPackingLine(_Base):
    __tablename__ = "wb_fbo_packing_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbo_packing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sku: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    box_human_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    package_code: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    item_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scan_keys_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    label_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=LINE_PENDING)
    printed_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _parse_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


@dataclass
class WbFboPackingLineRow:
    id: int
    job_id: int
    seq: int
    sku: str
    product_id: int | None
    product_name: str
    box_human_id: str
    package_code: str
    item_qty: int
    scan_keys: list[str]
    label_stored_name: str
    status: str
    printed_at_ts: int | None
    done_at_ts: int | None
    done_by_user_id: int | None

    @property
    def order_display(self) -> str:
        return self.box_human_id or str(self.seq)


@dataclass
class WbFboPackingJobRow:
    id: int
    supply_id: str
    warehouse_name: str
    city: str
    seller_name: str
    plan_date: str
    pallet_count: int
    supply_qr_code: str
    supply_qr_stored_name: str
    pallet_sheets_stored_name: str
    box_labels_stored_name: str
    status: str
    created_by_user_id: int | None
    warnings: list[str]
    created_at_ts: int
    updated_at_ts: int
    packer_user_ids: list[int]
    packer_names: list[str]
    line_total: int
    line_done: int
    line_pending: int
    line_printed: int
    lines: list[WbFboPackingLineRow] = field(default_factory=list)


class WbFboPackingRepository:
    def __init__(self, db_url: str, *, files_data_dir: str | Path) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.file_storage = WarehouseTaskFileStorage(Path(files_data_dir))

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def _line_row(self, row: WbFboPackingLine) -> WbFboPackingLineRow:
        return WbFboPackingLineRow(
            id=int(row.id),
            job_id=int(row.job_id),
            seq=int(row.seq),
            sku=str(row.sku or ""),
            product_id=int(row.product_id) if row.product_id else None,
            product_name=str(row.product_name or ""),
            box_human_id=str(row.box_human_id or ""),
            package_code=str(row.package_code or ""),
            item_qty=int(row.item_qty or 1),
            scan_keys=_parse_json_list(row.scan_keys_json),
            label_stored_name=str(row.label_stored_name or ""),
            status=str(row.status or LINE_PENDING),
            printed_at_ts=int(row.printed_at_ts) if row.printed_at_ts else None,
            done_at_ts=int(row.done_at_ts) if row.done_at_ts else None,
            done_by_user_id=int(row.done_by_user_id) if row.done_by_user_id else None,
        )

    def _counts(self, session: Session, job_id: int) -> tuple[int, int, int, int]:
        rows = session.scalars(
            select(WbFboPackingLine).where(WbFboPackingLine.job_id == int(job_id))
        ).all()
        total = len(rows)
        done = sum(1 for r in rows if r.status == LINE_DONE)
        printed = sum(1 for r in rows if r.status == LINE_PRINTED)
        pending = sum(1 for r in rows if r.status == LINE_PENDING)
        return total, done, pending, printed

    def _job_row(
        self,
        session: Session,
        job: WbFboPackingJob,
        *,
        include_lines: bool = False,
        packer_names: dict[int, str] | None = None,
    ) -> WbFboPackingJobRow:
        assignees = session.scalars(
            select(WbFboPackingJobAssignee).where(
                WbFboPackingJobAssignee.job_id == int(job.id)
            )
        ).all()
        packer_ids = [int(a.user_id) for a in assignees]
        names = [packer_names[i] for i in packer_ids if packer_names and i in packer_names]
        total, done, pending, printed = self._counts(session, int(job.id))
        lines: list[WbFboPackingLineRow] = []
        if include_lines:
            line_rows = session.scalars(
                select(WbFboPackingLine)
                .where(WbFboPackingLine.job_id == int(job.id))
                .order_by(WbFboPackingLine.seq, WbFboPackingLine.id)
            ).all()
            lines = [self._line_row(item) for item in line_rows]
        return WbFboPackingJobRow(
            id=int(job.id),
            supply_id=str(job.supply_id or ""),
            warehouse_name=str(job.warehouse_name or ""),
            city=str(job.city or ""),
            seller_name=str(job.seller_name or ""),
            plan_date=str(job.plan_date or ""),
            pallet_count=int(job.pallet_count or 1),
            supply_qr_code=str(job.supply_qr_code or ""),
            supply_qr_stored_name=str(job.supply_qr_stored_name or ""),
            pallet_sheets_stored_name=str(job.pallet_sheets_stored_name or ""),
            box_labels_stored_name=str(job.box_labels_stored_name or ""),
            status=str(job.status or JOB_STATUS_OPEN),
            created_by_user_id=int(job.created_by_user_id) if job.created_by_user_id else None,
            warnings=_parse_json_list(job.warnings_json),
            created_at_ts=int(job.created_at_ts or 0),
            updated_at_ts=int(job.updated_at_ts or 0),
            packer_user_ids=packer_ids,
            packer_names=names,
            line_total=total,
            line_done=done,
            line_pending=pending,
            line_printed=printed,
            lines=lines,
        )

    def create_job(
        self,
        *,
        supply_id: str,
        warehouse_name: str,
        city: str,
        seller_name: str,
        plan_date: str,
        pallet_count: int,
        supply_qr_code: str,
        created_by_user_id: int | None,
        packer_user_ids: list[int],
        warnings: list[str] | None = None,
        supply_qr_pdf: bytes,
        pallet_sheets_pdf: bytes,
        box_labels_pdf: bytes | None,
        lines: list[dict[str, Any]],
    ) -> WbFboPackingJobRow:
        if not lines:
            raise ValueError("В поставке нет коробов для задания")
        now = int(time.time())
        stored: list[str] = []
        try:
            qr_name, _ = self.file_storage.store_pdf(
                content=supply_qr_pdf, original_filename="supply_qr.pdf"
            )
            stored.append(qr_name)
            sheets_name, _ = self.file_storage.store_pdf(
                content=pallet_sheets_pdf, original_filename="pallet_sheets.pdf"
            )
            stored.append(sheets_name)
            labels_name = ""
            if box_labels_pdf:
                labels_name, _ = self.file_storage.store_pdf(
                    content=box_labels_pdf, original_filename="box_labels.pdf"
                )
                stored.append(labels_name)
            stored_lines: list[tuple[dict[str, Any], str]] = []
            for item in lines:
                pdf = item.get("pdf") or b""
                name, _ = self.file_storage.store_pdf(
                    content=pdf,
                    original_filename=f"box_{item.get('seq') or item.get('box_human_id')}.pdf",
                )
                stored.append(name)
                stored_lines.append((item, name))
        except Exception:
            for name in stored:
                self.file_storage.delete_stored(name)
            raise

        with Session(self.engine) as session:
            job = WbFboPackingJob(
                supply_id=str(supply_id or ""),
                warehouse_name=str(warehouse_name or ""),
                city=str(city or ""),
                seller_name=str(seller_name or ""),
                plan_date=str(plan_date or ""),
                pallet_count=int(pallet_count or 1),
                supply_qr_code=str(supply_qr_code or ""),
                supply_qr_stored_name=qr_name,
                pallet_sheets_stored_name=sheets_name,
                box_labels_stored_name=labels_name,
                status=JOB_STATUS_OPEN,
                created_by_user_id=int(created_by_user_id) if created_by_user_id else None,
                warnings_json=json.dumps(warnings or [], ensure_ascii=False),
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(job)
            session.flush()
            seen: set[int] = set()
            for user_id in packer_user_ids:
                try:
                    uid = int(user_id)
                except (TypeError, ValueError):
                    continue
                if uid <= 0 or uid in seen:
                    continue
                seen.add(uid)
                session.add(WbFboPackingJobAssignee(job_id=int(job.id), user_id=uid))
            for seq, (item, name) in enumerate(stored_lines, start=1):
                product_id = item.get("product_id")
                session.add(
                    WbFboPackingLine(
                        job_id=int(job.id),
                        seq=int(item.get("seq") or seq),
                        sku=str(item.get("sku") or ""),
                        product_id=int(product_id) if product_id else None,
                        product_name=str(item.get("product_name") or ""),
                        box_human_id=str(item.get("box_human_id") or ""),
                        package_code=str(item.get("package_code") or ""),
                        item_qty=int(item.get("item_qty") or 1),
                        scan_keys_json=json.dumps(item.get("scan_keys") or [], ensure_ascii=False),
                        label_stored_name=name,
                        status=LINE_PENDING,
                    )
                )
            session.commit()
            session.refresh(job)
            return self._job_row(session, job, include_lines=True)

    def list_jobs(
        self,
        *,
        limit: int = 50,
        packer_names: dict[int, str] | None = None,
    ) -> list[WbFboPackingJobRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WbFboPackingJob)
                .order_by(WbFboPackingJob.id.desc())
                .limit(max(1, min(int(limit), 200)))
            ).all()
            return [self._job_row(session, row, packer_names=packer_names) for row in rows]

    def list_my_jobs(self, user_id: int) -> list[WbFboPackingJobRow]:
        with Session(self.engine) as session:
            job_ids = session.scalars(
                select(WbFboPackingJobAssignee.job_id).where(
                    WbFboPackingJobAssignee.user_id == int(user_id)
                )
            ).all()
            if not job_ids:
                return []
            rows = session.scalars(
                select(WbFboPackingJob)
                .where(
                    WbFboPackingJob.id.in_([int(x) for x in job_ids]),
                    WbFboPackingJob.status.in_(JOB_ACTIVE_STATUSES),
                )
                .order_by(WbFboPackingJob.id.desc())
            ).all()
            return [self._job_row(session, row) for row in rows]

    def get_job(self, job_id: int, *, include_lines: bool = False) -> WbFboPackingJobRow | None:
        with Session(self.engine) as session:
            job = session.get(WbFboPackingJob, int(job_id))
            if job is None:
                return None
            return self._job_row(session, job, include_lines=include_lines)

    def user_can_pack(self, job_id: int, user_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WbFboPackingJobAssignee, (int(job_id), int(user_id)))
            return row is not None

    def cancel_job(self, job_id: int) -> WbFboPackingJobRow | None:
        with Session(self.engine) as session:
            job = session.get(WbFboPackingJob, int(job_id))
            if job is None:
                return None
            if job.status == JOB_STATUS_DONE:
                raise ValueError("Задание уже выполнено")
            job.status = JOB_STATUS_CANCELLED
            job.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(job)
            return self._job_row(session, job)

    def remaining_groups_from_lines(self, lines: list[WbFboPackingLineRow]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for line in lines:
            if line.status != LINE_PENDING:
                continue
            key = line.sku.casefold() or f"#{line.id}"
            item = grouped.get(key)
            if item is None:
                item = {
                    "sku": line.sku,
                    "product_id": line.product_id,
                    "name": line.product_name,
                    "quantity": 0,
                }
                grouped[key] = item
                order.append(key)
            item["quantity"] += 1
        return [grouped[key] for key in order]

    def _require_active_job(self, session: Session, job_id: int) -> WbFboPackingJob:
        job = session.get(WbFboPackingJob, int(job_id))
        if job is None:
            raise ValueError("Задание не найдено")
        if job.status == JOB_STATUS_CANCELLED:
            raise ValueError("Задание отменено")
        if job.status == JOB_STATUS_DONE:
            raise ValueError("Задание уже выполнено")
        return job

    def _printed_lines(self, session: Session, job_id: int) -> list[WbFboPackingLine]:
        return list(
            session.scalars(
                select(WbFboPackingLine)
                .where(
                    WbFboPackingLine.job_id == int(job_id),
                    WbFboPackingLine.status == LINE_PRINTED,
                )
                .order_by(WbFboPackingLine.seq, WbFboPackingLine.id)
            ).all()
        )

    def _line_matches(
        self,
        line: WbFboPackingLine,
        *,
        sku: str,
        product_id: int | None,
        barcode: str = "",
    ) -> bool:
        if product_id and line.product_id and int(line.product_id) == int(product_id):
            return True
        want = str(sku or "").strip().casefold()
        if want and str(line.sku or "").strip().casefold() == want:
            return True
        code = str(barcode or "").strip().casefold()
        if code:
            keys = [str(k).strip().casefold() for k in _parse_json_list(line.scan_keys_json)]
            if code in keys:
                return True
        return False

    def _finish_if_complete(self, session: Session, job: WbFboPackingJob) -> None:
        pending = session.scalars(
            select(WbFboPackingLine.id).where(
                WbFboPackingLine.job_id == int(job.id),
                WbFboPackingLine.status != LINE_DONE,
            )
        ).first()
        if pending is None:
            job.status = JOB_STATUS_DONE
            job.updated_at_ts = int(time.time())

    def allocate_lines(
        self,
        job_id: int,
        user_id: int,
        *,
        sku: str = "",
        product_id: int | None = None,
        barcode: str = "",
        batch: bool = False,
        auto_close: bool = False,
    ) -> list[WbFboPackingLineRow]:
        if not str(sku or "").strip() and not product_id and not str(barcode or "").strip():
            raise ValueError("Нет артикула для выделения")
        now = int(time.time())
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            printed_rows = self._printed_lines(session, job_id)
            foreign = [
                row
                for row in printed_rows
                if not self._line_matches(row, sku=sku, product_id=product_id, barcode=barcode)
            ]
            if foreign:
                raise ValueError(
                    "Сначала наклейте ярлык активного короба или отмените печать"
                )
            pending = list(
                session.scalars(
                    select(WbFboPackingLine)
                    .where(
                        WbFboPackingLine.job_id == int(job_id),
                        WbFboPackingLine.status == LINE_PENDING,
                    )
                    .order_by(WbFboPackingLine.seq, WbFboPackingLine.id)
                ).all()
            )
            matched = [
                row
                for row in pending
                if self._line_matches(row, sku=sku, product_id=product_id, barcode=barcode)
            ]
            if not matched:
                raise ValueError("Нет коробов с этим товаром")
            chosen = matched if batch else matched[:1]
            status = LINE_DONE if auto_close else LINE_PRINTED
            for row in chosen:
                row.status = status
                row.printed_at_ts = now
                if auto_close:
                    row.done_at_ts = now
                    row.done_by_user_id = int(user_id)
            if job.status == JOB_STATUS_OPEN:
                job.status = JOB_STATUS_IN_PROGRESS
            job.updated_at_ts = now
            if auto_close:
                self._finish_if_complete(session, job)
            session.commit()
            return [self._line_row(row) for row in chosen]

    def cancel_print(self, job_id: int, line_id: int) -> WbFboPackingLineRow:
        with Session(self.engine) as session:
            self._require_active_job(session, job_id)
            row = session.get(WbFboPackingLine, int(line_id))
            if row is None or int(row.job_id) != int(job_id):
                raise ValueError("Строка не найдена")
            if row.status != LINE_PRINTED:
                raise ValueError("Отменить можно только напечатанный ярлык")
            row.status = LINE_PENDING
            row.printed_at_ts = None
            session.commit()
            return self._line_row(row)

    def close_line(self, job_id: int, line_id: int, user_id: int) -> WbFboPackingLineRow:
        now = int(time.time())
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            row = session.get(WbFboPackingLine, int(line_id))
            if row is None or int(row.job_id) != int(job_id):
                raise ValueError("Строка не найдена")
            if row.status not in {LINE_PRINTED, LINE_PENDING}:
                raise ValueError("Строка уже закрыта")
            row.status = LINE_DONE
            row.done_at_ts = now
            row.done_by_user_id = int(user_id)
            if not row.printed_at_ts:
                row.printed_at_ts = now
            self._finish_if_complete(session, job)
            session.commit()
            return self._line_row(row)

    def set_line_status(
        self, job_id: int, line_id: int, user_id: int, status: str
    ) -> WbFboPackingLineRow:
        want = str(status or "").strip().lower()
        if want == LINE_DONE:
            return self.close_line(job_id, line_id, user_id)
        if want == LINE_PENDING:
            return self.cancel_print(job_id, line_id)
        raise ValueError("Некорректный статус")

    def read_line_pdf(self, job_id: int, line_id: int) -> bytes:
        with Session(self.engine) as session:
            row = session.get(WbFboPackingLine, int(line_id))
            if row is None or int(row.job_id) != int(job_id):
                raise ValueError("Ярлык не найден")
            path = self.file_storage.path_for(row.label_stored_name)
            if path is None:
                raise ValueError("Файл ярлыка не найден")
            return path.read_bytes()

    def list_line_pdfs(self, job_id: int) -> list[tuple[int, bytes]]:
        job = self.get_job(job_id, include_lines=True)
        if job is None:
            raise ValueError("Задание не найдено")
        out: list[tuple[int, bytes]] = []
        for line in job.lines:
            path = self.file_storage.path_for(line.label_stored_name)
            if path is None:
                continue
            out.append((line.id, path.read_bytes()))
        return out

    def read_job_pdf(self, job_id: int, kind: str) -> bytes:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("Задание не найдено")
        stored = {
            "supply-qr": job.supply_qr_stored_name,
            "pallet-sheets": job.pallet_sheets_stored_name,
            "box-labels": job.box_labels_stored_name,
        }.get(kind, "")
        if not stored:
            raise ValueError("Файл не найден")
        path = self.file_storage.path_for(stored)
        if path is None:
            raise ValueError("Файл не найден")
        return path.read_bytes()

    def line_to_dict(self, line: WbFboPackingLineRow) -> dict[str, Any]:
        return {
            "id": line.id,
            "job_id": line.job_id,
            "seq": line.seq,
            "sku": line.sku,
            "product_id": line.product_id,
            "product_name": line.product_name,
            "order_id": line.box_human_id,
            "order_display": line.order_display,
            "box_id": line.box_human_id,
            "package_code": line.package_code,
            "place_index": line.seq,
            "place_total": 1,
            "quantity": line.item_qty,
            "status": line.status,
            "cis_key": "",
            "cis_gtin": "",
            "has_cis": False,
        }

    def job_to_dict(self, job: WbFboPackingJobRow, *, include_lines: bool = False) -> dict[str, Any]:
        payload = {
            "id": job.id,
            "marketplace": "wildberries",
            "supply_id": job.supply_id,
            "warehouse_name": job.warehouse_name,
            "city": job.city,
            "seller_name": job.seller_name,
            "plan_date": job.plan_date,
            "pallet_count": job.pallet_count,
            "supply_qr_code": job.supply_qr_code,
            "has_supply_qr": bool(job.supply_qr_stored_name),
            "has_pallet_sheets": bool(job.pallet_sheets_stored_name),
            "has_box_labels": bool(job.box_labels_stored_name),
            "status": job.status,
            "created_by_user_id": job.created_by_user_id,
            "warnings": job.warnings,
            "created_at_ts": job.created_at_ts,
            "updated_at_ts": job.updated_at_ts,
            "packer_user_ids": job.packer_user_ids,
            "packer_names": job.packer_names,
            "line_total": job.line_total,
            "line_done": job.line_done,
            "line_pending": job.line_pending,
            "line_printed": job.line_printed,
            "remaining": job.line_pending,
        }
        if include_lines:
            payload["lines"] = [self.line_to_dict(line) for line in job.lines]
            active_lines = [
                self.line_to_dict(line) for line in job.lines if line.status == LINE_PRINTED
            ]
            payload["active_lines"] = active_lines
            payload["active_line"] = active_lines[0] if active_lines else None
        return payload
