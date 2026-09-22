"""Задания упаковки прочих маркетплейсов. Сейчас — ВсеИнструменты."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db import create_db_engine

PLATFORM_VSEINSTRUMENTI = "vseinstrumenti"

JOB_OPEN = "open"
JOB_IN_PROGRESS = "in_progress"
JOB_DONE = "done"
JOB_CANCELLED = "cancelled"
JOB_ACTIVE = (JOB_OPEN, JOB_IN_PROGRESS)

LINE_PENDING = "pending"
LINE_DONE = "done"


class _Base(DeclarativeBase):
    pass


class OtherMarketplaceJob(_Base):
    __tablename__ = "other_marketplace_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default=PLATFORM_VSEINSTRUMENTI)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    transfer_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    supplier: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    delivery_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    purchase_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_filename: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JOB_OPEN)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OtherMarketplaceAssignee(_Base):
    __tablename__ = "other_marketplace_assignees"

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class OtherMarketplaceLine(_Base):
    __tablename__ = "other_marketplace_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sku: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    excel_barcode: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    picked_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    require_cis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=LINE_PENDING)


class OtherMarketplaceCis(_Base):
    __tablename__ = "other_marketplace_cis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_jobs.id", ondelete="CASCADE"), nullable=False
    )
    line_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_lines.id", ondelete="CASCADE"), nullable=False
    )
    cis_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    cis_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cis_gtin: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class OtherMarketplaceBarcodeMismatch(_Base):
    __tablename__ = "other_marketplace_barcode_mismatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_jobs.id", ondelete="CASCADE"), nullable=False
    )
    line_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("other_marketplace_lines.id", ondelete="CASCADE"), nullable=False
    )
    scanned_barcode: Mapped[str] = mapped_column(String(128), nullable=False, default="")


@dataclass
class OtherMarketplaceCisRow:
    id: int
    line_id: int
    cis_key: str
    cis_raw: str
    cis_gtin: str


@dataclass
class OtherMarketplaceMismatchRow:
    id: int
    line_id: int
    scanned_barcode: str


@dataclass
class OtherMarketplaceLineRow:
    id: int
    job_id: int
    seq: int
    sku: str
    product_id: int | None
    product_name: str
    excel_barcode: str
    quantity: int
    picked_qty: int
    require_cis: bool
    status: str
    cis: list[OtherMarketplaceCisRow] = field(default_factory=list)
    mismatches: list[OtherMarketplaceMismatchRow] = field(default_factory=list)


@dataclass
class OtherMarketplaceJobRow:
    id: int
    platform: str
    order_number: str
    transfer_number: str
    supplier: str
    delivery_date: str
    purchase_status: str
    source_filename: str
    status: str
    created_by_user_id: int | None
    created_at_ts: int
    updated_at_ts: int
    packer_user_ids: list[int] = field(default_factory=list)
    lines: list[OtherMarketplaceLineRow] = field(default_factory=list)


class OtherMarketplaceRepository:
    def __init__(self, db_url: str) -> None:
        self.engine = create_db_engine(db_url)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._migrate_purchase_status()

    def _migrate_purchase_status(self) -> None:
        from sqlalchemy import inspect, text

        tables = set(inspect(self.engine).get_table_names())
        if "other_marketplace_jobs" not in tables:
            return
        columns = {col["name"] for col in inspect(self.engine).get_columns("other_marketplace_jobs")}
        if "purchase_status" in columns:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE other_marketplace_jobs "
                    "ADD COLUMN purchase_status VARCHAR(64) NOT NULL DEFAULT ''"
                )
            )
            session.commit()

    def create_job(
        self,
        *,
        platform: str,
        order_number: str,
        transfer_number: str,
        supplier: str,
        delivery_date: str,
        purchase_status: str,
        source_filename: str,
        created_by_user_id: int | None,
        packer_user_ids: list[int],
        lines: list[dict],
    ) -> OtherMarketplaceJobRow:
        now = int(time.time())
        with Session(self.engine) as session:
            job = OtherMarketplaceJob(
                platform=platform,
                order_number=order_number,
                transfer_number=transfer_number,
                supplier=supplier,
                delivery_date=delivery_date,
                purchase_status=str(purchase_status or "")[:64],
                source_filename=source_filename,
                status=JOB_OPEN,
                created_by_user_id=created_by_user_id,
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(job)
            session.flush()
            for user_id in packer_user_ids:
                session.add(OtherMarketplaceAssignee(job_id=int(job.id), user_id=int(user_id)))
            for seq, item in enumerate(lines, start=1):
                session.add(
                    OtherMarketplaceLine(
                        job_id=int(job.id),
                        seq=seq,
                        sku=str(item.get("sku") or ""),
                        product_id=item.get("product_id"),
                        product_name=str(item.get("product_name") or ""),
                        excel_barcode=str(item.get("excel_barcode") or ""),
                        quantity=int(item.get("quantity") or 0),
                        picked_qty=0,
                        require_cis=bool(item.get("require_cis")),
                        status=LINE_PENDING,
                    )
                )
            session.commit()
            job_id = int(job.id)
        row = self.get_job(job_id)
        if row is None:
            raise ValueError("Задание не сохранилось")
        return row

    def list_jobs(self, platform: str) -> list[OtherMarketplaceJobRow]:
        with Session(self.engine) as session:
            jobs = session.scalars(
                select(OtherMarketplaceJob)
                .where(OtherMarketplaceJob.platform == platform)
                .order_by(OtherMarketplaceJob.id.desc())
            ).all()
            return [self._job_row(session, job, include_lines=False) for job in jobs]

    def list_my_jobs(self, user_id: int) -> list[OtherMarketplaceJobRow]:
        with Session(self.engine) as session:
            job_ids = session.scalars(
                select(OtherMarketplaceAssignee.job_id).where(
                    OtherMarketplaceAssignee.user_id == int(user_id)
                )
            ).all()
            if not job_ids:
                return []
            jobs = session.scalars(
                select(OtherMarketplaceJob)
                .where(
                    OtherMarketplaceJob.id.in_(list(job_ids)),
                    OtherMarketplaceJob.status.in_(JOB_ACTIVE),
                )
                .order_by(OtherMarketplaceJob.id.desc())
            ).all()
            return [self._job_row(session, job, include_lines=False) for job in jobs]

    def get_job(self, job_id: int, *, include_lines: bool = True) -> OtherMarketplaceJobRow | None:
        with Session(self.engine) as session:
            job = session.get(OtherMarketplaceJob, int(job_id))
            if job is None:
                return None
            return self._job_row(session, job, include_lines=include_lines)

    def cancel_job(self, job_id: int) -> OtherMarketplaceJobRow:
        with Session(self.engine) as session:
            job = session.get(OtherMarketplaceJob, int(job_id))
            if job is None:
                raise ValueError("Задание не найдено")
            if job.status == JOB_DONE:
                raise ValueError("Задание уже собрано")
            job.status = JOB_CANCELLED
            job.updated_at_ts = int(time.time())
            session.commit()
        row = self.get_job(job_id)
        if row is None:
            raise ValueError("Задание не найдено")
            return row

    def set_purchase_status(self, job_id: int, purchase_status: str) -> OtherMarketplaceJobRow:
        with Session(self.engine) as session:
            job = session.get(OtherMarketplaceJob, int(job_id))
            if job is None:
                raise ValueError("Задание не найдено")
            job.purchase_status = str(purchase_status or "")[:64]
            job.updated_at_ts = int(time.time())
            session.commit()
        row = self.get_job(job_id, include_lines=False)
        if row is None:
            raise ValueError("Задание не найдено")
        return row

    def set_line_status(self, job_id: int, line_id: int, status: str) -> OtherMarketplaceJobRow:
        want = str(status or "").strip().casefold()
        if want not in {LINE_PENDING, LINE_DONE}:
            raise ValueError("Можно поставить только статус «в сборке» или «готово»")
        now = int(time.time())
        with Session(self.engine) as session:
            job = session.get(OtherMarketplaceJob, int(job_id))
            line = session.get(OtherMarketplaceLine, int(line_id))
            if job is None or line is None or int(line.job_id) != int(job.id):
                raise ValueError("Строка задания не найдена")
            if job.status == JOB_CANCELLED:
                raise ValueError("Задание отменено")
            if want == LINE_DONE:
                line.status = LINE_DONE
                line.picked_qty = int(line.quantity)
            else:
                line.status = LINE_PENDING
                if int(line.picked_qty) >= int(line.quantity):
                    line.picked_qty = 0
            pending = session.scalar(
                select(OtherMarketplaceLine.id).where(
                    OtherMarketplaceLine.job_id == int(job.id),
                    OtherMarketplaceLine.status != LINE_DONE,
                )
            )
            if pending is None:
                job.status = JOB_DONE
            elif job.status in {JOB_OPEN, JOB_DONE}:
                job.status = JOB_IN_PROGRESS
            job.updated_at_ts = now
            session.commit()
        row = self.get_job(job_id)
        if row is None:
            raise ValueError("Задание не найдено")
        return row

    def record_pick(
        self,
        job_id: int,
        line_id: int,
        *,
        add_qty: int,
        cis_key: str = "",
        cis_raw: str = "",
        cis_gtin: str = "",
        mismatch_barcode: str = "",
    ) -> OtherMarketplaceLineRow:
        now = int(time.time())
        with Session(self.engine) as session:
            job = session.get(OtherMarketplaceJob, int(job_id))
            line = session.get(OtherMarketplaceLine, int(line_id))
            if job is None or line is None or int(line.job_id) != int(job.id):
                raise ValueError("Строка задания не найдена")
            if job.status == JOB_CANCELLED:
                raise ValueError("Задание отменено")
            if job.status == JOB_DONE:
                raise ValueError("Задание уже собрано")
            if cis_key:
                taken = session.scalar(
                    select(OtherMarketplaceCis.id).where(
                        OtherMarketplaceCis.job_id == int(job.id),
                        OtherMarketplaceCis.cis_key == cis_key,
                    )
                )
                if taken is not None:
                    raise ValueError("Этот КИЗ уже отсканирован")
                session.add(
                    OtherMarketplaceCis(
                        job_id=int(job.id),
                        line_id=int(line.id),
                        cis_key=cis_key,
                        cis_raw=cis_raw,
                        cis_gtin=cis_gtin,
                    )
                )
            if mismatch_barcode:
                session.add(
                    OtherMarketplaceBarcodeMismatch(
                        job_id=int(job.id),
                        line_id=int(line.id),
                        scanned_barcode=mismatch_barcode[:128],
                    )
                )
            line.picked_qty = min(int(line.quantity), int(line.picked_qty) + max(0, int(add_qty)))
            if line.picked_qty >= int(line.quantity) and int(line.quantity) > 0:
                line.status = LINE_DONE
            if job.status == JOB_OPEN:
                job.status = JOB_IN_PROGRESS
            pending = session.scalar(
                select(OtherMarketplaceLine.id).where(
                    OtherMarketplaceLine.job_id == int(job.id),
                    OtherMarketplaceLine.status != LINE_DONE,
                )
            )
            if pending is None:
                job.status = JOB_DONE
            job.updated_at_ts = now
            session.commit()
            line_id_saved = int(line.id)
        job_row = self.get_job(job_id)
        if job_row is None:
            raise ValueError("Задание не найдено")
        for line_row in job_row.lines:
            if line_row.id == line_id_saved:
                return line_row
        raise ValueError("Строка задания не найдена")

    def job_to_dict(
        self,
        row: OtherMarketplaceJobRow,
        *,
        include_lines: bool = True,
        packer_names: dict[int, str] | None = None,
    ) -> dict:
        names = packer_names or {}
        done = sum(1 for line in row.lines if line.status == LINE_DONE)
        payload = {
            "id": row.id,
            "platform": row.platform,
            "order_number": row.order_number,
            "transfer_number": row.transfer_number,
            "supplier": row.supplier,
            "delivery_date": row.delivery_date,
            "purchase_status": row.purchase_status,
            "source_filename": row.source_filename,
            "status": row.status,
            "created_by_user_id": row.created_by_user_id,
            "created_at_ts": row.created_at_ts,
            "packer_user_ids": list(row.packer_user_ids),
            "packer_names": [names.get(uid, str(uid)) for uid in row.packer_user_ids],
            "line_total": len(row.lines),
            "line_done": done,
        }
        if include_lines:
            payload["lines"] = [
                {
                    "id": line.id,
                    "seq": line.seq,
                    "sku": line.sku,
                    "product_id": line.product_id,
                    "product_name": line.product_name,
                    "excel_barcode": line.excel_barcode,
                    "quantity": line.quantity,
                    "picked_qty": line.picked_qty,
                    "remaining": max(0, line.quantity - line.picked_qty),
                    "require_cis": line.require_cis,
                    "status": line.status,
                    "cis": [
                        {"cis_key": item.cis_key, "cis_raw": item.cis_raw, "cis_gtin": item.cis_gtin}
                        for item in line.cis
                    ],
                    "mismatches": [item.scanned_barcode for item in line.mismatches],
                }
                for line in row.lines
            ]
            payload["remaining_groups"] = [
                {
                    "line_id": line.id,
                    "sku": line.sku,
                    "product_id": line.product_id,
                    "name": line.product_name,
                    "barcode": line.excel_barcode,
                    "qty": max(0, line.quantity - line.picked_qty),
                    "quantity": max(0, line.quantity - line.picked_qty),
                    "require_cis": line.require_cis,
                }
                for line in row.lines
                if line.status != LINE_DONE
            ]
        return payload

    def _job_row(
        self,
        session: Session,
        job: OtherMarketplaceJob,
        *,
        include_lines: bool,
    ) -> OtherMarketplaceJobRow:
        assignees = session.scalars(
            select(OtherMarketplaceAssignee.user_id).where(
                OtherMarketplaceAssignee.job_id == int(job.id)
            )
        ).all()
        lines: list[OtherMarketplaceLineRow] = []
        if include_lines:
            raw_lines = session.scalars(
                select(OtherMarketplaceLine)
                .where(OtherMarketplaceLine.job_id == int(job.id))
                .order_by(OtherMarketplaceLine.seq, OtherMarketplaceLine.id)
            ).all()
            cis_rows = session.scalars(
                select(OtherMarketplaceCis).where(OtherMarketplaceCis.job_id == int(job.id))
            ).all()
            mismatch_rows = session.scalars(
                select(OtherMarketplaceBarcodeMismatch).where(
                    OtherMarketplaceBarcodeMismatch.job_id == int(job.id)
                )
            ).all()
            cis_by_line: dict[int, list[OtherMarketplaceCisRow]] = {}
            for item in cis_rows:
                cis_by_line.setdefault(int(item.line_id), []).append(
                    OtherMarketplaceCisRow(
                        id=int(item.id),
                        line_id=int(item.line_id),
                        cis_key=item.cis_key,
                        cis_raw=item.cis_raw,
                        cis_gtin=item.cis_gtin,
                    )
                )
            mismatch_by_line: dict[int, list[OtherMarketplaceMismatchRow]] = {}
            for item in mismatch_rows:
                mismatch_by_line.setdefault(int(item.line_id), []).append(
                    OtherMarketplaceMismatchRow(
                        id=int(item.id),
                        line_id=int(item.line_id),
                        scanned_barcode=item.scanned_barcode,
                    )
                )
            for line in raw_lines:
                lines.append(
                    OtherMarketplaceLineRow(
                        id=int(line.id),
                        job_id=int(line.job_id),
                        seq=int(line.seq),
                        sku=line.sku,
                        product_id=int(line.product_id) if line.product_id is not None else None,
                        product_name=line.product_name,
                        excel_barcode=line.excel_barcode,
                        quantity=int(line.quantity),
                        picked_qty=int(line.picked_qty),
                        require_cis=bool(line.require_cis),
                        status=line.status,
                        cis=cis_by_line.get(int(line.id), []),
                        mismatches=mismatch_by_line.get(int(line.id), []),
                    )
                )
        else:
            raw_lines = session.scalars(
                select(OtherMarketplaceLine).where(OtherMarketplaceLine.job_id == int(job.id))
            ).all()
            lines = [
                OtherMarketplaceLineRow(
                    id=int(line.id),
                    job_id=int(line.job_id),
                    seq=int(line.seq),
                    sku=line.sku,
                    product_id=int(line.product_id) if line.product_id is not None else None,
                    product_name=line.product_name,
                    excel_barcode=line.excel_barcode,
                    quantity=int(line.quantity),
                    picked_qty=int(line.picked_qty),
                    require_cis=bool(line.require_cis),
                    status=line.status,
                )
                for line in raw_lines
            ]
        return OtherMarketplaceJobRow(
            id=int(job.id),
            platform=job.platform,
            order_number=job.order_number,
            transfer_number=job.transfer_number,
            supplier=job.supplier,
            delivery_date=job.delivery_date,
            purchase_status=str(job.purchase_status or ""),
            source_filename=job.source_filename,
            status=job.status,
            created_by_user_id=int(job.created_by_user_id) if job.created_by_user_id is not None else None,
            created_at_ts=int(job.created_at_ts),
            updated_at_ts=int(job.updated_at_ts),
            packer_user_ids=[int(uid) for uid in assignees],
            lines=lines,
        )
