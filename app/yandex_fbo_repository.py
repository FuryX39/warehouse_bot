"""Задания FBO-упаковки Яндекс Маркета (FBY): товары, грузоместа, назначения."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_task_files import WarehouseTaskFileStorage

JOB_STATUS_OPEN = "open"
JOB_STATUS_IN_PROGRESS = "in_progress"
JOB_STATUS_DONE = "done"
JOB_STATUS_CANCELLED = "cancelled"
JOB_ACTIVE_STATUSES = (JOB_STATUS_OPEN, JOB_STATUS_IN_PROGRESS)


class _Base(DeclarativeBase):
    pass


class YandexFboJob(_Base):
    __tablename__ = "yandex_fbo_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    marketplace_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    warehouse_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parent_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vrc_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    transit_warehouse: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    transit_at: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    accept_at: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    supply_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cargo_labels_stored_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JOB_STATUS_OPEN)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class YandexFboJobAssignee(_Base):
    __tablename__ = "yandex_fbo_job_assignees"

    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class YandexFboProduct(_Base):
    __tablename__ = "yandex_fbo_products"
    __table_args__ = (UniqueConstraint("job_id", "sku", name="uq_yandex_fbo_product_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sku: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    catalog_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    planned_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scan_keys_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class YandexFboCargo(_Base):
    __tablename__ = "yandex_fbo_cargoes"
    __table_args__ = (
        UniqueConstraint("job_id", "cargo_code", name="uq_yandex_fbo_cargo_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cargo_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class YandexFboCargoItem(_Base):
    __tablename__ = "yandex_fbo_cargo_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_jobs.id", ondelete="CASCADE"), nullable=False
    )
    cargo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_cargoes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("yandex_fbo_products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


@dataclass(frozen=True)
class YandexFboCargoItemRow:
    id: int
    cargo_id: int
    product_id: int
    sku: str
    product_name: str
    quantity: int
    assigned_at_ts: int
    assigned_by_user_id: int | None


@dataclass(frozen=True)
class YandexFboCargoRow:
    id: int
    seq: int
    page_index: int
    cargo_code: str
    assigned_qty: int
    items: list[YandexFboCargoItemRow] = field(default_factory=list)


@dataclass(frozen=True)
class YandexFboProductRow:
    id: int
    seq: int
    sku: str
    catalog_product_id: int | None
    product_name: str
    planned_qty: int
    assigned_qty: int
    remaining_qty: int
    scan_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class YandexFboJobRow:
    id: int
    request_id: int
    marketplace_request_id: str
    warehouse_request_id: str
    parent_request_id: int | None
    vrc_label: str
    warehouse_name: str
    transit_warehouse: str
    transit_at: str
    accept_at: str
    supply_status: str
    cargo_labels_stored_name: str
    status: str
    created_by_user_id: int | None
    created_at_ts: int
    updated_at_ts: int
    packer_user_ids: list[int] = field(default_factory=list)
    packer_names: list[str] = field(default_factory=list)
    line_total: int = 0
    line_done: int = 0
    line_pending: int = 0
    cargo_total: int = 0
    cargo_used: int = 0
    products: list[YandexFboProductRow] = field(default_factory=list)
    cargoes: list[YandexFboCargoRow] = field(default_factory=list)


def _json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _norm_code(value: str) -> str:
    return str(value or "").strip()


class YandexFboRepository:
    def __init__(self, db_url: str, *, files_data_dir: Path) -> None:
        from app.db import create_db_engine

        self.engine = create_db_engine(db_url)
        self.files = WarehouseTaskFileStorage(Path(files_data_dir))

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def read_stored(self, stored_name: str) -> bytes:
        path = self.files.path_for(stored_name)
        if path is None:
            raise ValueError("Файл задания не найден")
        return path.read_bytes()

    def read_job_labels_pdf(self, job_id: int) -> bytes:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("Задание не найдено")
        if not job.cargo_labels_stored_name:
            raise ValueError("PDF грузомест не сохранён")
        return self.read_stored(job.cargo_labels_stored_name)

    def find_active_by_request_id(self, request_id: int) -> YandexFboJobRow | None:
        with Session(self.engine) as session:
            job = session.scalar(
                select(YandexFboJob).where(
                    YandexFboJob.request_id == int(request_id),
                    YandexFboJob.status.in_(JOB_ACTIVE_STATUSES + (JOB_STATUS_DONE,)),
                )
            )
            if job is None:
                return None
            return self._job_row(session, job)

    def list_jobs(
        self,
        *,
        packer_names: dict[int, str] | None = None,
        limit: int = 200,
    ) -> list[YandexFboJobRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(YandexFboJob)
                .order_by(YandexFboJob.id.desc())
                .limit(max(1, min(int(limit), 200)))
            ).all()
            return [
                self._job_row(session, row, packer_names=packer_names) for row in rows
            ]

    def list_my_jobs(self, user_id: int) -> list[YandexFboJobRow]:
        with Session(self.engine) as session:
            job_ids = session.scalars(
                select(YandexFboJobAssignee.job_id).where(
                    YandexFboJobAssignee.user_id == int(user_id)
                )
            ).all()
            if not job_ids:
                return []
            rows = session.scalars(
                select(YandexFboJob)
                .where(
                    YandexFboJob.id.in_([int(x) for x in job_ids]),
                    YandexFboJob.status.in_(JOB_ACTIVE_STATUSES),
                )
                .order_by(YandexFboJob.id.desc())
            ).all()
            return [self._job_row(session, row) for row in rows]

    def get_job(self, job_id: int, *, include_details: bool = False) -> YandexFboJobRow | None:
        with Session(self.engine) as session:
            job = session.get(YandexFboJob, int(job_id))
            if job is None:
                return None
            return self._job_row(session, job, include_details=include_details)

    def user_can_pack(self, job_id: int, user_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(YandexFboJobAssignee, (int(job_id), int(user_id)))
            return row is not None

    def set_assignees(self, job_id: int, user_ids: list[int]) -> YandexFboJobRow:
        ids = list(dict.fromkeys(int(value) for value in user_ids if int(value) > 0))
        if not ids:
            raise ValueError("Назначьте хотя бы одного упаковщика")
        with Session(self.engine) as session:
            job = session.get(YandexFboJob, int(job_id))
            if job is None:
                raise ValueError("Задание не найдено")
            session.execute(
                delete(YandexFboJobAssignee).where(
                    YandexFboJobAssignee.job_id == int(job_id)
                )
            )
            session.add_all(
                [YandexFboJobAssignee(job_id=int(job_id), user_id=uid) for uid in ids]
            )
            job.updated_at_ts = int(time.time())
            session.commit()
        row = self.get_job(job_id)
        if row is None:
            raise ValueError("Задание не найдено")
        return row

    def cancel_job(self, job_id: int) -> YandexFboJobRow | None:
        with Session(self.engine) as session:
            job = session.get(YandexFboJob, int(job_id))
            if job is None:
                return None
            if job.status == JOB_STATUS_CANCELLED:
                return self._job_row(session, job)
            job.status = JOB_STATUS_CANCELLED
            job.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(job)
            return self._job_row(session, job)

    def create_job(
        self,
        *,
        request_id: int,
        marketplace_request_id: str,
        warehouse_request_id: str = "",
        parent_request_id: int | None = None,
        vrc_label: str = "",
        warehouse_name: str = "",
        transit_warehouse: str = "",
        transit_at: str = "",
        accept_at: str = "",
        supply_status: str = "",
        packer_user_ids: list[int],
        created_by_user_id: int | None,
        products: list[dict[str, Any]],
        cargoes: list[dict[str, Any]],
        cargo_labels_pdf: bytes,
    ) -> YandexFboJobRow:
        ids = list(dict.fromkeys(int(value) for value in packer_user_ids if int(value) > 0))
        if not ids:
            raise ValueError("Назначьте хотя бы одного упаковщика")
        if not products:
            raise ValueError("Товары поставки ещё недоступны")
        if not cargoes:
            raise ValueError("Коды грузомест ещё недоступны")
        existing = self.find_active_by_request_id(int(request_id))
        if existing is not None:
            raise ValueError(f"Задание для заявки {request_id} уже создано (#{existing.id})")

        stored_name, _size = self.files.store_pdf(
            content=cargo_labels_pdf,
            original_filename=f"yandex-fbo-{request_id}-cargo-units.pdf",
        )
        now = int(time.time())
        try:
            with Session(self.engine) as session:
                job = YandexFboJob(
                    request_id=int(request_id),
                    marketplace_request_id=str(marketplace_request_id or "").strip(),
                    warehouse_request_id=str(warehouse_request_id or "").strip(),
                    parent_request_id=int(parent_request_id) if parent_request_id else None,
                    vrc_label=str(vrc_label or "").strip(),
                    warehouse_name=str(warehouse_name or "").strip(),
                    transit_warehouse=str(transit_warehouse or "").strip(),
                    transit_at=str(transit_at or "").strip(),
                    accept_at=str(accept_at or "").strip(),
                    supply_status=str(supply_status or "").strip(),
                    cargo_labels_stored_name=stored_name,
                    status=JOB_STATUS_OPEN,
                    created_by_user_id=int(created_by_user_id) if created_by_user_id else None,
                    created_at_ts=now,
                    updated_at_ts=now,
                )
                session.add(job)
                session.flush()
                session.add_all(
                    [
                        YandexFboJobAssignee(job_id=int(job.id), user_id=uid)
                        for uid in ids
                    ]
                )
                sku_seen: set[str] = set()
                for seq, item in enumerate(products, start=1):
                    sku = str(item.get("sku") or "").strip()
                    planned = int(item.get("planned_qty") or 0)
                    if not sku or planned <= 0:
                        raise ValueError("Товары поставки ещё недоступны")
                    key = sku.casefold()
                    if key in sku_seen:
                        raise ValueError(f"Артикул {sku} повторяется в составе поставки")
                    sku_seen.add(key)
                    keys = [
                        str(value).strip()
                        for value in (item.get("scan_keys") or [])
                        if str(value).strip()
                    ]
                    if sku not in keys:
                        keys.insert(0, sku)
                    catalog_id = item.get("catalog_product_id")
                    session.add(
                        YandexFboProduct(
                            job_id=int(job.id),
                            seq=seq,
                            sku=sku,
                            catalog_product_id=int(catalog_id) if catalog_id else None,
                            product_name=str(item.get("product_name") or sku).strip(),
                            planned_qty=planned,
                            scan_keys_json=json.dumps(list(dict.fromkeys(keys)), ensure_ascii=False),
                        )
                    )
                code_seen: set[str] = set()
                for seq, cargo in enumerate(cargoes, start=1):
                    code = str(cargo.get("cargo_code") or "").strip().upper()
                    if not code:
                        raise ValueError("Коды грузомест ещё недоступны")
                    if code in code_seen:
                        raise ValueError(f"Код грузоместа {code} повторяется")
                    code_seen.add(code)
                    session.add(
                        YandexFboCargo(
                            job_id=int(job.id),
                            seq=seq,
                            page_index=int(cargo.get("page_index") or seq - 1),
                            cargo_code=code,
                        )
                    )
                session.commit()
                job_id = int(job.id)
        except Exception:
            self.files.delete_stored(stored_name)
            raise
        row = self.get_job(job_id, include_details=True)
        if row is None:
            raise ValueError("Задание не найдено")
        return row

    def resolve_scan(self, job_id: int, barcode: str) -> dict[str, Any]:
        code = _norm_code(barcode)
        if not code:
            raise ValueError("Пустой штрихкод")
        job = self.get_job(job_id, include_details=True)
        if job is None:
            raise ValueError("Задание не найдено")
        if job.status == JOB_STATUS_CANCELLED:
            raise ValueError("Задание отменено")
        cargo = self._find_cargo(job, code)
        if cargo is not None:
            return {"kind": "cargo", "cargo": self._cargo_dict(cargo)}
        matches = self._find_products(job, code)
        if len(matches) > 1:
            skus = ", ".join(item.sku for item in matches)
            raise ValueError(f"Штрихкод {code} совпадает с несколькими товарами: {skus}")
        if len(matches) == 1:
            return {"kind": "product", "product": self._product_dict(matches[0])}
        raise ValueError(f"Штрихкод {code} не найден в задании")

    def assign(
        self,
        job_id: int,
        user_id: int,
        *,
        product_id: int,
        quantity: int,
        cargo_code: str,
    ) -> YandexFboJobRow:
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("Количество должно быть больше нуля")
        code = str(cargo_code or "").strip().upper()
        if not code:
            raise ValueError("Укажите код грузоместа")
        now = int(time.time())
        with Session(self.engine) as session:
            job = session.get(YandexFboJob, int(job_id))
            if job is None:
                raise ValueError("Задание не найдено")
            if job.status == JOB_STATUS_CANCELLED:
                raise ValueError("Задание отменено")
            if job.status == JOB_STATUS_DONE:
                raise ValueError("Задание уже завершено")
            product = session.get(YandexFboProduct, int(product_id))
            if product is None or int(product.job_id) != int(job_id):
                raise ValueError("Товар не найден в задании")
            cargo = session.scalar(
                select(YandexFboCargo).where(
                    YandexFboCargo.job_id == int(job_id),
                    YandexFboCargo.cargo_code == code,
                )
            )
            if cargo is None:
                raise ValueError(f"Грузоместо {code} не найдено в задании")
            assigned_qty = sum(
                int(row.quantity)
                for row in session.scalars(
                    select(YandexFboCargoItem).where(
                        YandexFboCargoItem.product_id == int(product.id)
                    )
                ).all()
            )
            remaining = int(product.planned_qty) - assigned_qty
            if qty > remaining:
                raise ValueError(
                    f"Нельзя назначить {qty} шт.: остаток {product.sku} — {remaining}"
                )
            session.add(
                YandexFboCargoItem(
                    job_id=int(job_id),
                    cargo_id=int(cargo.id),
                    product_id=int(product.id),
                    quantity=qty,
                    assigned_at_ts=now,
                    assigned_by_user_id=int(user_id),
                )
            )
            totals = self._assigned_totals(session, int(job_id))
            planned_total = sum(
                int(row.planned_qty)
                for row in session.scalars(
                    select(YandexFboProduct).where(YandexFboProduct.job_id == int(job_id))
                ).all()
            )
            if job.status == JOB_STATUS_OPEN:
                job.status = JOB_STATUS_IN_PROGRESS
            if planned_total > 0 and totals.get("qty", 0) >= planned_total:
                job.status = JOB_STATUS_DONE
            job.updated_at_ts = now
            session.commit()
        row = self.get_job(job_id, include_details=True)
        if row is None:
            raise ValueError("Задание не найдено")
        return row

    def match_catalog_product(
        self,
        job: YandexFboJobRow,
        *,
        catalog_product_id: int | None,
        sku: str = "",
    ) -> YandexFboProductRow | None:
        if catalog_product_id:
            for item in job.products:
                if item.catalog_product_id == int(catalog_product_id):
                    return item
        key = str(sku or "").strip().casefold()
        if not key:
            return None
        found = [item for item in job.products if item.sku.casefold() == key]
        return found[0] if len(found) == 1 else None

    def job_to_dict(self, job: YandexFboJobRow, *, include_details: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": job.id,
            "marketplace": "yandex",
            "platform": "yandex",
            "supply_id": job.marketplace_request_id or str(job.request_id),
            "request_id": job.request_id,
            "marketplace_request_id": job.marketplace_request_id,
            "warehouse_request_id": job.warehouse_request_id,
            "parent_request_id": job.parent_request_id,
            "vrc_label": job.vrc_label,
            "display_id": " / ".join(
                part
                for part in (
                    job.marketplace_request_id or str(job.request_id),
                    job.vrc_label,
                )
                if part
            ),
            "warehouse_name": job.warehouse_name,
            "transit_warehouse": job.transit_warehouse,
            "transit_at": job.transit_at,
            "accept_at": job.accept_at,
            "supply_status": job.supply_status,
            "has_cargo_labels": bool(job.cargo_labels_stored_name),
            "status": job.status,
            "created_by_user_id": job.created_by_user_id,
            "created_at_ts": job.created_at_ts,
            "updated_at_ts": job.updated_at_ts,
            "packer_user_ids": job.packer_user_ids,
            "packer_names": job.packer_names,
            "line_total": job.line_total,
            "line_done": job.line_done,
            "line_pending": job.line_pending,
            "remaining": job.line_pending,
            "cargo_total": job.cargo_total,
            "cargo_used": job.cargo_used,
        }
        if include_details:
            payload["products"] = [self._product_dict(item) for item in job.products]
            payload["remaining_groups"] = [
                self._product_dict(item) for item in job.products if item.remaining_qty > 0
            ]
            payload["cargoes"] = [self._cargo_dict(item) for item in job.cargoes]
        return payload

    def _product_dict(self, item: YandexFboProductRow) -> dict[str, Any]:
        barcode = next((key for key in item.scan_keys if key.casefold() != item.sku.casefold()), "")
        return {
            "id": item.id,
            "product_id": item.id,
            "catalog_product_id": item.catalog_product_id,
            "sku": item.sku,
            "name": item.product_name,
            "product_name": item.product_name,
            "planned_qty": item.planned_qty,
            "assigned_qty": item.assigned_qty,
            "remaining_qty": item.remaining_qty,
            "remaining": item.remaining_qty,
            "barcode": barcode or item.sku,
            "scan_keys": item.scan_keys,
        }

    def _cargo_dict(self, item: YandexFboCargoRow) -> dict[str, Any]:
        return {
            "id": item.id,
            "seq": item.seq,
            "page_index": item.page_index,
            "cargo_code": item.cargo_code,
            "assigned_qty": item.assigned_qty,
            "items": [
                {
                    "id": row.id,
                    "product_id": row.product_id,
                    "sku": row.sku,
                    "name": row.product_name,
                    "quantity": row.quantity,
                    "assigned_at_ts": row.assigned_at_ts,
                    "assigned_by_user_id": row.assigned_by_user_id,
                }
                for row in item.items
            ],
        }

    def _find_cargo(self, job: YandexFboJobRow, code: str) -> YandexFboCargoRow | None:
        needle = code.strip().upper()
        for cargo in job.cargoes:
            if cargo.cargo_code.upper() == needle:
                return cargo
        return None

    def _find_products(self, job: YandexFboJobRow, code: str) -> list[YandexFboProductRow]:
        needle = code.strip().casefold()
        found: list[YandexFboProductRow] = []
        for product in job.products:
            keys = [product.sku.casefold(), *(key.casefold() for key in product.scan_keys)]
            if needle in keys:
                found.append(product)
        return found

    def _assigned_totals(self, session: Session, job_id: int) -> dict[str, int]:
        items = session.scalars(
            select(YandexFboCargoItem).where(YandexFboCargoItem.job_id == int(job_id))
        ).all()
        return {"qty": sum(int(row.quantity) for row in items), "rows": len(items)}

    def _job_row(
        self,
        session: Session,
        job: YandexFboJob,
        *,
        packer_names: dict[int, str] | None = None,
        include_details: bool = False,
    ) -> YandexFboJobRow:
        assignee_ids = [
            int(row.user_id)
            for row in session.scalars(
                select(YandexFboJobAssignee).where(YandexFboJobAssignee.job_id == int(job.id))
            ).all()
        ]
        names = []
        if packer_names:
            names = [packer_names[uid] for uid in assignee_ids if uid in packer_names]
        products = session.scalars(
            select(YandexFboProduct)
            .where(YandexFboProduct.job_id == int(job.id))
            .order_by(YandexFboProduct.seq, YandexFboProduct.id)
        ).all()
        cargoes = session.scalars(
            select(YandexFboCargo)
            .where(YandexFboCargo.job_id == int(job.id))
            .order_by(YandexFboCargo.seq, YandexFboCargo.id)
        ).all()
        items = session.scalars(
            select(YandexFboCargoItem).where(YandexFboCargoItem.job_id == int(job.id))
        ).all()
        qty_by_product: dict[int, int] = {}
        qty_by_cargo: dict[int, int] = {}
        items_by_cargo: dict[int, list[YandexFboCargoItem]] = {}
        for row in items:
            qty_by_product[int(row.product_id)] = qty_by_product.get(int(row.product_id), 0) + int(
                row.quantity
            )
            qty_by_cargo[int(row.cargo_id)] = qty_by_cargo.get(int(row.cargo_id), 0) + int(
                row.quantity
            )
            items_by_cargo.setdefault(int(row.cargo_id), []).append(row)
        product_by_id = {int(row.id): row for row in products}
        product_rows: list[YandexFboProductRow] = []
        planned_total = 0
        done_total = 0
        for row in products:
            assigned = qty_by_product.get(int(row.id), 0)
            planned = int(row.planned_qty)
            planned_total += planned
            done_total += min(assigned, planned)
            product_rows.append(
                YandexFboProductRow(
                    id=int(row.id),
                    seq=int(row.seq),
                    sku=str(row.sku or ""),
                    catalog_product_id=int(row.catalog_product_id)
                    if row.catalog_product_id
                    else None,
                    product_name=str(row.product_name or ""),
                    planned_qty=planned,
                    assigned_qty=assigned,
                    remaining_qty=max(0, planned - assigned),
                    scan_keys=_json_list(row.scan_keys_json),
                )
            )
        cargo_rows: list[YandexFboCargoRow] = []
        used = 0
        for row in cargoes:
            assigned = qty_by_cargo.get(int(row.id), 0)
            if assigned > 0:
                used += 1
            cargo_item_rows = []
            if include_details:
                for item in items_by_cargo.get(int(row.id), []):
                    product = product_by_id.get(int(item.product_id))
                    cargo_item_rows.append(
                        YandexFboCargoItemRow(
                            id=int(item.id),
                            cargo_id=int(item.cargo_id),
                            product_id=int(item.product_id),
                            sku=str(product.sku if product else ""),
                            product_name=str(product.product_name if product else ""),
                            quantity=int(item.quantity),
                            assigned_at_ts=int(item.assigned_at_ts),
                            assigned_by_user_id=int(item.assigned_by_user_id)
                            if item.assigned_by_user_id
                            else None,
                        )
                    )
            cargo_rows.append(
                YandexFboCargoRow(
                    id=int(row.id),
                    seq=int(row.seq),
                    page_index=int(row.page_index),
                    cargo_code=str(row.cargo_code or ""),
                    assigned_qty=assigned,
                    items=cargo_item_rows,
                )
            )
        return YandexFboJobRow(
            id=int(job.id),
            request_id=int(job.request_id),
            marketplace_request_id=str(job.marketplace_request_id or ""),
            warehouse_request_id=str(job.warehouse_request_id or ""),
            parent_request_id=int(job.parent_request_id) if job.parent_request_id else None,
            vrc_label=str(job.vrc_label or ""),
            warehouse_name=str(job.warehouse_name or ""),
            transit_warehouse=str(job.transit_warehouse or ""),
            transit_at=str(job.transit_at or ""),
            accept_at=str(job.accept_at or ""),
            supply_status=str(job.supply_status or ""),
            cargo_labels_stored_name=str(job.cargo_labels_stored_name or ""),
            status=str(job.status or JOB_STATUS_OPEN),
            created_by_user_id=int(job.created_by_user_id) if job.created_by_user_id else None,
            created_at_ts=int(job.created_at_ts or 0),
            updated_at_ts=int(job.updated_at_ts or 0),
            packer_user_ids=assignee_ids,
            packer_names=names,
            line_total=planned_total,
            line_done=done_total,
            line_pending=max(0, planned_total - done_total),
            cargo_total=len(cargo_rows),
            cargo_used=used,
            products=product_rows if include_details else [],
            cargoes=cargo_rows if include_details else cargo_rows,
        )
