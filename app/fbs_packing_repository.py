"""Задания FBS-упаковки: строки с ярлыками на каждую товарную единицу."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_task_files import WarehouseTaskFileStorage
from app.yandex_label_sorter import parse_sheet_label_key

JOB_STATUS_OPEN = "open"
JOB_STATUS_IN_PROGRESS = "in_progress"
JOB_STATUS_DONE = "done"
JOB_STATUS_CANCELLED = "cancelled"
JOB_ACTIVE_STATUSES = (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS)

LINE_PENDING = "pending"
LINE_PRINTED = "printed"
LINE_DONE = "done"

MARKETPLACE_YANDEX = "yandex"


class _Base(DeclarativeBase):
    pass


class FbsPackingJob(_Base):
    __tablename__ = "fbs_packing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, default=MARKETPLACE_YANDEX)
    order_substatus: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    build_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JOB_STATUS_OPEN)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    sheet_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    merged_label_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FbsPackingJobAssignee(_Base):
    __tablename__ = "fbs_packing_job_assignees"

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fbs_packing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class FbsPackingLine(_Base):
    __tablename__ = "fbs_packing_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fbs_packing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sku: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    box_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    place_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    place_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scan_keys_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    label_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=LINE_PENDING)
    printed_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_at_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


@dataclass
class FbsPackingLineRow:
    id: int
    job_id: int
    seq: int
    sku: str
    product_id: int | None
    product_name: str
    order_id: str
    box_id: int | None
    place_index: int
    place_total: int
    scan_keys: list[str]
    label_stored_name: str
    status: str
    printed_at_ts: int | None
    done_at_ts: int | None
    done_by_user_id: int | None

    @property
    def order_display(self) -> str:
        if self.place_total > 1:
            return f"{self.order_id} {self.place_index}/{self.place_total}"
        return self.order_id


@dataclass
class FbsPackingJobRow:
    id: int
    marketplace: str
    order_substatus: str
    build_list: bool
    status: str
    created_by_user_id: int | None
    sheet_url: str
    sheet_title: str
    merged_label_stored_name: str
    warnings: list[str]
    created_at_ts: int
    updated_at_ts: int
    packer_user_ids: list[int]
    packer_names: list[str]
    line_total: int
    line_done: int
    line_pending: int
    line_printed: int
    lines: list[FbsPackingLineRow] = field(default_factory=list)


def _parse_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def scan_matches_line(raw: str, line: FbsPackingLineRow) -> bool:
    """Сверка пика ярлыка только с активной строкой."""
    text = str(raw or "").strip()
    if not text:
        return False
    compact = text.replace(" ", "")
    for key in line.scan_keys:
        if text == key or compact == str(key).replace(" ", ""):
            return True
    if line.box_id is not None and compact == str(line.box_id):
        return True
    parsed = parse_sheet_label_key(text)
    if parsed is None:
        return False
    if parsed.order_id != line.order_id:
        return False
    if parsed.place_total <= 1:
        return True
    return parsed.place_index == int(line.place_index)


class FbsPackingRepository:
    def __init__(self, db_url: str, *, files_data_dir: str | Path) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.file_storage = WarehouseTaskFileStorage(Path(files_data_dir))

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def _line_row(self, row: FbsPackingLine) -> FbsPackingLineRow:
        return FbsPackingLineRow(
            id=int(row.id),
            job_id=int(row.job_id),
            seq=int(row.seq),
            sku=str(row.sku or ""),
            product_id=int(row.product_id) if row.product_id else None,
            product_name=str(row.product_name or ""),
            order_id=str(row.order_id or ""),
            box_id=int(row.box_id) if row.box_id else None,
            place_index=int(row.place_index or 1),
            place_total=int(row.place_total or 1),
            scan_keys=_parse_json_list(row.scan_keys_json),
            label_stored_name=str(row.label_stored_name or ""),
            status=str(row.status or LINE_PENDING),
            printed_at_ts=int(row.printed_at_ts) if row.printed_at_ts else None,
            done_at_ts=int(row.done_at_ts) if row.done_at_ts else None,
            done_by_user_id=int(row.done_by_user_id) if row.done_by_user_id else None,
        )

    def _counts(self, session: Session, job_id: int) -> tuple[int, int, int, int]:
        rows = session.scalars(select(FbsPackingLine).where(FbsPackingLine.job_id == int(job_id))).all()
        total = len(rows)
        done = sum(1 for r in rows if r.status == LINE_DONE)
        printed = sum(1 for r in rows if r.status == LINE_PRINTED)
        pending = sum(1 for r in rows if r.status == LINE_PENDING)
        return total, done, pending, printed

    def _job_row(
        self,
        session: Session,
        job: FbsPackingJob,
        *,
        include_lines: bool = False,
        packer_names: dict[int, str] | None = None,
    ) -> FbsPackingJobRow:
        assignees = session.scalars(
            select(FbsPackingJobAssignee).where(FbsPackingJobAssignee.job_id == int(job.id))
        ).all()
        packer_ids = [int(a.user_id) for a in assignees]
        names = []
        if packer_names:
            names = [packer_names[i] for i in packer_ids if i in packer_names]
        total, done, pending, printed = self._counts(session, int(job.id))
        lines: list[FbsPackingLineRow] = []
        if include_lines:
            line_rows = session.scalars(
                select(FbsPackingLine)
                .where(FbsPackingLine.job_id == int(job.id))
                .order_by(FbsPackingLine.seq, FbsPackingLine.id)
            ).all()
            lines = [self._line_row(item) for item in line_rows]
        return FbsPackingJobRow(
            id=int(job.id),
            marketplace=str(job.marketplace or MARKETPLACE_YANDEX),
            order_substatus=str(job.order_substatus or "STARTED"),
            build_list=bool(job.build_list),
            status=str(job.status or JOB_STATUS_OPEN),
            created_by_user_id=int(job.created_by_user_id) if job.created_by_user_id else None,
            sheet_url=str(job.sheet_url or ""),
            sheet_title=str(job.sheet_title or ""),
            merged_label_stored_name=str(job.merged_label_stored_name or ""),
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
        marketplace: str,
        order_substatus: str,
        build_list: bool,
        created_by_user_id: int | None,
        packer_user_ids: list[int],
        sheet_url: str = "",
        sheet_title: str = "",
        warnings: list[str] | None = None,
        merged_pdf: bytes | None = None,
        lines: list[dict[str, Any]],
    ) -> FbsPackingJobRow:
        if not lines:
            raise ValueError("Нет строк с ярлыками для задания")
        now = int(time.time())
        merged_name = ""
        if merged_pdf:
            merged_name, _ = self.file_storage.store_pdf(
                content=merged_pdf,
                original_filename="yandex_fbs_labels.pdf",
            )
        stored_lines: list[tuple[dict[str, Any], str]] = []
        try:
            for item in lines:
                pdf = item.get("pdf") or b""
                stored, _ = self.file_storage.store_pdf(
                    content=pdf,
                    original_filename=f"yandex_{item.get('order_id')}_{item.get('box_id') or item.get('seq')}.pdf",
                )
                stored_lines.append((item, stored))
        except Exception:
            if merged_name:
                self.file_storage.delete_stored(merged_name)
            for _, stored in stored_lines:
                self.file_storage.delete_stored(stored)
            raise

        with Session(self.engine) as session:
            job = FbsPackingJob(
                marketplace=str(marketplace or MARKETPLACE_YANDEX),
                order_substatus=str(order_substatus or "STARTED"),
                build_list=bool(build_list),
                status=JOB_STATUS_OPEN,
                created_by_user_id=int(created_by_user_id) if created_by_user_id else None,
                sheet_url=str(sheet_url or ""),
                sheet_title=str(sheet_title or ""),
                merged_label_stored_name=merged_name,
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
                session.add(FbsPackingJobAssignee(job_id=int(job.id), user_id=uid))
            for seq, (item, stored) in enumerate(stored_lines, start=1):
                product_id = item.get("product_id")
                box_id = item.get("box_id")
                session.add(
                    FbsPackingLine(
                        job_id=int(job.id),
                        seq=int(item.get("seq") or seq),
                        sku=str(item.get("sku") or ""),
                        product_id=int(product_id) if product_id else None,
                        product_name=str(item.get("product_name") or ""),
                        order_id=str(item.get("order_id") or ""),
                        box_id=int(box_id) if box_id else None,
                        place_index=int(item.get("place_index") or 1),
                        place_total=int(item.get("place_total") or 1),
                        scan_keys_json=json.dumps(item.get("scan_keys") or [], ensure_ascii=False),
                        label_stored_name=stored,
                        status=LINE_PENDING,
                    )
                )
            session.commit()
            session.refresh(job)
            return self._job_row(session, job, include_lines=True)

    def list_jobs(
        self, *, limit: int = 50, packer_names: dict[int, str] | None = None
    ) -> list[FbsPackingJobRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(FbsPackingJob)
                .order_by(FbsPackingJob.id.desc())
                .limit(max(1, min(int(limit), 200)))
            ).all()
            return [self._job_row(session, row, packer_names=packer_names) for row in rows]

    def list_my_jobs(self, user_id: int) -> list[FbsPackingJobRow]:
        with Session(self.engine) as session:
            job_ids = session.scalars(
                select(FbsPackingJobAssignee.job_id).where(
                    FbsPackingJobAssignee.user_id == int(user_id)
                )
            ).all()
            if not job_ids:
                return []
            rows = session.scalars(
                select(FbsPackingJob)
                .where(
                    FbsPackingJob.id.in_([int(x) for x in job_ids]),
                    FbsPackingJob.status.in_(JOB_ACTIVE_STATUSES),
                )
                .order_by(FbsPackingJob.id.desc())
            ).all()
            return [self._job_row(session, row) for row in rows]

    def get_job(self, job_id: int, *, include_lines: bool = False) -> FbsPackingJobRow | None:
        with Session(self.engine) as session:
            job = session.get(FbsPackingJob, int(job_id))
            if job is None:
                return None
            return self._job_row(session, job, include_lines=include_lines)

    def user_can_pack(self, job_id: int, user_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(FbsPackingJobAssignee, (int(job_id), int(user_id)))
            return row is not None

    def cancel_job(self, job_id: int) -> FbsPackingJobRow | None:
        with Session(self.engine) as session:
            job = session.get(FbsPackingJob, int(job_id))
            if job is None:
                return None
            if job.status == JOB_STATUS_DONE:
                raise ValueError("Задание уже выполнено")
            job.status = JOB_STATUS_CANCELLED
            job.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(job)
            return self._job_row(session, job)

    def remaining_groups(self, job_id: int) -> list[dict[str, Any]]:
        job = self.get_job(job_id, include_lines=True)
        if job is None:
            raise ValueError("Задание не найдено")
        grouped: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for line in job.lines:
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

    def _require_active_job(self, session: Session, job_id: int) -> FbsPackingJob:
        job = session.get(FbsPackingJob, int(job_id))
        if job is None:
            raise ValueError("Задание не найдено")
        if job.status == JOB_STATUS_CANCELLED:
            raise ValueError("Задание отменено")
        if job.status == JOB_STATUS_DONE:
            raise ValueError("Задание уже выполнено")
        return job

    def _printed_line(self, session: Session, job_id: int) -> FbsPackingLine | None:
        return session.scalar(
            select(FbsPackingLine).where(
                FbsPackingLine.job_id == int(job_id),
                FbsPackingLine.status == LINE_PRINTED,
            )
        )

    def _sku_matches(self, line: FbsPackingLine, *, sku: str, product_id: int | None) -> bool:
        if product_id and line.product_id and int(line.product_id) == int(product_id):
            return True
        want = str(sku or "").strip().casefold()
        if want and str(line.sku or "").strip().casefold() == want:
            return True
        return False

    def allocate_line(
        self,
        job_id: int,
        user_id: int,
        *,
        sku: str = "",
        product_id: int | None = None,
    ) -> FbsPackingLineRow:
        if not str(sku or "").strip() and not product_id:
            raise ValueError("Нет артикула для выделения")
        now = int(time.time())
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            printed = self._printed_line(session, job_id)
            if printed is not None:
                if self._sku_matches(printed, sku=sku, product_id=product_id):
                    return self._line_row(printed)
                raise ValueError(
                    "Сначала наклейте ярлык активного товара, закройте строку вручную или отмените печать"
                )
            pending = session.scalars(
                select(FbsPackingLine)
                .where(
                    FbsPackingLine.job_id == int(job_id),
                    FbsPackingLine.status == LINE_PENDING,
                )
                .order_by(FbsPackingLine.seq, FbsPackingLine.id)
            ).all()
            match = next(
                (row for row in pending if self._sku_matches(row, sku=sku, product_id=product_id)),
                None,
            )
            if match is None:
                raise ValueError("Этого товара нет среди оставшихся в задании")
            match.status = LINE_PRINTED
            match.printed_at_ts = now
            if job.status == JOB_STATUS_OPEN:
                job.status = JOB_STATUS_IN_PROGRESS
            job.updated_at_ts = now
            session.commit()
            session.refresh(match)
            return self._line_row(match)

    def _finish_if_complete(self, session: Session, job: FbsPackingJob) -> None:
        remaining = session.scalar(
            select(func.count())
            .select_from(FbsPackingLine)
            .where(
                FbsPackingLine.job_id == int(job.id),
                FbsPackingLine.status != LINE_DONE,
            )
        )
        if int(remaining or 0) == 0:
            job.status = JOB_STATUS_DONE

    def scan_label(self, job_id: int, user_id: int, raw: str) -> FbsPackingLineRow:
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            printed = self._printed_line(session, job_id)
            if printed is None:
                raise ValueError("Нет активного товара — сначала спикайте товар")
            line = self._line_row(printed)
            if not scan_matches_line(raw, line):
                raise ValueError("Ярлык не совпадает с активным заказом")
            printed.status = LINE_DONE
            printed.done_at_ts = int(time.time())
            printed.done_by_user_id = int(user_id)
            job.updated_at_ts = int(time.time())
            self._finish_if_complete(session, job)
            session.commit()
            session.refresh(printed)
            return self._line_row(printed)

    def close_line(self, job_id: int, line_id: int, user_id: int) -> FbsPackingLineRow:
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            printed = self._printed_line(session, job_id)
            if printed is None or int(printed.id) != int(line_id):
                raise ValueError("Закрыть можно только активную напечатанную строку")
            printed.status = LINE_DONE
            printed.done_at_ts = int(time.time())
            printed.done_by_user_id = int(user_id)
            job.updated_at_ts = int(time.time())
            self._finish_if_complete(session, job)
            session.commit()
            session.refresh(printed)
            return self._line_row(printed)

    def cancel_print(self, job_id: int, line_id: int) -> FbsPackingLineRow:
        with Session(self.engine) as session:
            job = self._require_active_job(session, job_id)
            printed = self._printed_line(session, job_id)
            if printed is None or int(printed.id) != int(line_id):
                raise ValueError("Отменить можно только активную напечатанную строку")
            printed.status = LINE_PENDING
            printed.printed_at_ts = None
            job.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(printed)
            return self._line_row(printed)

    def get_line(self, job_id: int, line_id: int) -> FbsPackingLineRow | None:
        with Session(self.engine) as session:
            row = session.get(FbsPackingLine, int(line_id))
            if row is None or int(row.job_id) != int(job_id):
                return None
            return self._line_row(row)

    def read_line_pdf(self, job_id: int, line_id: int) -> bytes:
        line = self.get_line(job_id, line_id)
        if line is None:
            raise ValueError("Строка не найдена")
        path = self.file_storage.path_for(line.label_stored_name)
        if path is None:
            raise ValueError("PDF ярлыка не найден")
        return path.read_bytes()

    def read_merged_pdf(self, job_id: int) -> bytes:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("Задание не найдено")
        if not job.merged_label_stored_name:
            raise ValueError("Общий PDF не формировался")
        path = self.file_storage.path_for(job.merged_label_stored_name)
        if path is None:
            raise ValueError("Общий PDF не найден")
        return path.read_bytes()

    def job_to_dict(self, job: FbsPackingJobRow, *, include_lines: bool = False) -> dict[str, Any]:
        payload = {
            "id": job.id,
            "marketplace": job.marketplace,
            "order_substatus": job.order_substatus,
            "build_list": job.build_list,
            "status": job.status,
            "created_by_user_id": job.created_by_user_id,
            "sheet_url": job.sheet_url,
            "sheet_title": job.sheet_title,
            "has_merged_labels": bool(job.merged_label_stored_name),
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
            payload["active_line"] = next(
                (self.line_to_dict(line) for line in job.lines if line.status == LINE_PRINTED),
                None,
            )
        return payload

    def line_to_dict(self, line: FbsPackingLineRow) -> dict[str, Any]:
        return {
            "id": line.id,
            "job_id": line.job_id,
            "seq": line.seq,
            "sku": line.sku,
            "product_id": line.product_id,
            "product_name": line.product_name,
            "order_id": line.order_id,
            "order_display": line.order_display,
            "box_id": line.box_id,
            "place_index": line.place_index,
            "place_total": line.place_total,
            "quantity": 1,
            "status": line.status,
        }
