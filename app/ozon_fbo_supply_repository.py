"""Локальные задания FBO-поставок Ozon для менеджера и упаковщиков."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, delete, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogRepository
from app.warehouse_users_repository import WarehouseUsersRepository

SUPPLY_KIND_PALLET = "pallet"
SUPPLY_KIND_BOX = "box"
SUPPLY_KINDS = {SUPPLY_KIND_PALLET, SUPPLY_KIND_BOX}

STATUS_DRAFT = "draft"
STATUS_ASSIGNED = "assigned"
STATUS_PACKING = "packing"
STATUS_READY = "ready"
STATUS_SENT_TO_OZON = "sent_to_ozon"
STATUS_LABELS_READY = "labels_ready"
STATUS_DONE = "done"
STATUSES = {
    STATUS_DRAFT,
    STATUS_ASSIGNED,
    STATUS_PACKING,
    STATUS_READY,
    STATUS_SENT_TO_OZON,
    STATUS_LABELS_READY,
    STATUS_DONE,
}


class _Base(DeclarativeBase):
    pass


class OzonFboSupply(_Base):
    __tablename__ = "ozon_fbo_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    supply_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=SUPPLY_KIND_PALLET)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_DRAFT)
    ozon_supply_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_draft_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_cluster_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_cluster_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    ozon_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    assigned_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manager_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cargoes_operation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_operation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_file_guid: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_filename: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboSupplyItem(_Base):
    __tablename__ = "ozon_fbo_supply_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_supplies.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboCargo(_Base):
    __tablename__ = "ozon_fbo_cargoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_supplies.id", ondelete="CASCADE"), nullable=False
    )
    cargo_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_cargo_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboCargoItem(_Base):
    __tablename__ = "ozon_fbo_cargo_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cargo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_cargoes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expiration_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass
class FboSupplyItemRow:
    id: int
    product_id: int | None
    sku: str
    name: str
    quantity: int
    sort_order: int


@dataclass
class FboCargoItemRow:
    id: int
    product_id: int | None
    sku: str
    name: str
    quantity: int
    expiration_date: str
    sort_order: int


@dataclass
class FboCargoRow:
    id: int
    cargo_number: str
    ozon_cargo_id: str
    comment: str
    sort_order: int
    items: list[FboCargoItemRow] = field(default_factory=list)


@dataclass
class FboSupplyRow:
    id: int
    title: str
    supply_kind: str
    status: str
    ozon_supply_id: str
    ozon_draft_id: str
    ozon_cluster_id: str
    ozon_cluster_name: str
    ozon_warehouse_id: str
    ozon_warehouse_name: str
    assigned_user_id: int | None
    assigned_user_name: str
    manager_user_id: int | None
    manager_user_name: str
    cargoes_operation_id: str
    labels_operation_id: str
    labels_file_guid: str
    labels_filename: str
    comment: str
    created_at_ts: int
    updated_at_ts: int
    items: list[FboSupplyItemRow] = field(default_factory=list)
    cargoes: list[FboCargoRow] = field(default_factory=list)


def _str(raw: Any, limit: int = 512) -> str:
    return str(raw or "").strip()[:limit]


def _int_or_none(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _positive_int(raw: Any, *, default: int = 0) -> int:
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, val)


def _normalize_supply_kind(raw: Any) -> str:
    val = _str(raw, 16).lower()
    if val not in SUPPLY_KINDS:
        raise ValueError("Тип сборки должен быть pallet или box")
    return val


def _normalize_status(raw: Any, *, default: str = STATUS_DRAFT) -> str:
    val = _str(raw, 32).lower()
    if not val:
        return default
    if val not in STATUSES:
        raise ValueError("Некорректный статус FBO-заявки")
    return val


class OzonFboSupplyRepository:
    def __init__(
        self,
        db_url: str,
        catalog_repo: CatalogRepository,
        users_repo: WarehouseUsersRepository,
    ) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)
        self.catalog_repo = catalog_repo
        self.users_repo = users_repo

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        with self.engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(ozon_fbo_supplies)")).all()
            }
            if "cargoes_operation_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE ozon_fbo_supplies ADD COLUMN cargoes_operation_id "
                        "VARCHAR(128) NOT NULL DEFAULT ''"
                    )
                )
            if "labels_operation_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE ozon_fbo_supplies ADD COLUMN labels_operation_id "
                        "VARCHAR(128) NOT NULL DEFAULT ''"
                    )
                )

    def list_supplies(self, filters: dict[str, str] | None = None) -> list[FboSupplyRow]:
        filters = filters or {}
        with Session(self.engine) as session:
            q = select(OzonFboSupply).order_by(OzonFboSupply.updated_at_ts.desc(), OzonFboSupply.id.desc())
            conds = []
            status = _str(filters.get("status"), 32)
            if status:
                conds.append(OzonFboSupply.status == status)
            assigned_raw = filters.get("assigned_user_id")
            assigned_id = _int_or_none(assigned_raw)
            if assigned_id:
                conds.append(OzonFboSupply.assigned_user_id == assigned_id)
            q_text = _str(filters.get("q"), 128)
            if q_text:
                pat = f"%{q_text}%"
                from sqlalchemy import or_

                conds.append(
                    or_(
                        OzonFboSupply.title.ilike(pat),
                        OzonFboSupply.ozon_supply_id.ilike(pat),
                        OzonFboSupply.ozon_cluster_name.ilike(pat),
                        OzonFboSupply.ozon_warehouse_name.ilike(pat),
                    )
                )
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q.limit(500)).all()
            return [self._row(session, r, include_details=False) for r in rows]

    def get_supply(self, supply_id: int) -> FboSupplyRow | None:
        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return None
            return self._row(session, row, include_details=True)

    def create_supply(self, data: dict[str, Any], *, manager_user_id: int | None = None) -> FboSupplyRow:
        now = int(time.time())
        title = _str(data.get("title"), 256) or "FBO-поставка"
        with Session(self.engine) as session:
            row = OzonFboSupply(
                title=title,
                supply_kind=_normalize_supply_kind(data.get("supply_kind") or SUPPLY_KIND_PALLET),
                status=_normalize_status(data.get("status"), default=STATUS_DRAFT),
                ozon_supply_id=_str(data.get("ozon_supply_id"), 64),
                ozon_draft_id=_str(data.get("ozon_draft_id"), 64),
                ozon_cluster_id=_str(data.get("ozon_cluster_id"), 64),
                ozon_cluster_name=_str(data.get("ozon_cluster_name"), 256),
                ozon_warehouse_id=_str(data.get("ozon_warehouse_id"), 64),
                ozon_warehouse_name=_str(data.get("ozon_warehouse_name"), 256),
                assigned_user_id=self._validate_user_id(data.get("assigned_user_id")),
                manager_user_id=int(manager_user_id) if manager_user_id else None,
                comment=_str(data.get("comment"), 2048),
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.flush()
            self._replace_items(session, int(row.id), data.get("items") or [])
            session.commit()
            session.refresh(row)
            return self._row(session, row, include_details=True)

    def update_supply(self, supply_id: int, data: dict[str, Any]) -> FboSupplyRow | None:
        now = int(time.time())
        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return None
            for key, limit in (
                ("title", 256),
                ("ozon_supply_id", 64),
                ("ozon_draft_id", 64),
                ("ozon_cluster_id", 64),
                ("ozon_cluster_name", 256),
                ("ozon_warehouse_id", 64),
                ("ozon_warehouse_name", 256),
                ("cargoes_operation_id", 128),
                ("labels_operation_id", 128),
                ("labels_file_guid", 128),
                ("labels_filename", 256),
                ("comment", 2048),
            ):
                if key in data:
                    setattr(row, key, _str(data.get(key), limit))
            if "supply_kind" in data:
                row.supply_kind = _normalize_supply_kind(data.get("supply_kind"))
            if "status" in data:
                row.status = _normalize_status(data.get("status"), default=row.status)
            if "assigned_user_id" in data:
                row.assigned_user_id = self._validate_user_id(data.get("assigned_user_id"))
            if "items" in data:
                self._replace_items(session, int(row.id), data.get("items") or [])
            row.updated_at_ts = now
            session.commit()
            session.refresh(row)
            return self._row(session, row, include_details=True)

    def delete_supply(self, supply_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def save_cargoes(self, supply_id: int, cargoes: list[dict[str, Any]]) -> FboSupplyRow | None:
        with Session(self.engine) as session:
            supply = session.get(OzonFboSupply, int(supply_id))
            if supply is None:
                return None
            existing_ids = [
                int(r.id)
                for r in session.scalars(select(OzonFboCargo).where(OzonFboCargo.supply_id == int(supply_id))).all()
            ]
            if existing_ids:
                session.execute(delete(OzonFboCargoItem).where(OzonFboCargoItem.cargo_id.in_(existing_ids)))
            session.execute(delete(OzonFboCargo).where(OzonFboCargo.supply_id == int(supply_id)))
            for idx, raw in enumerate(cargoes or []):
                if not isinstance(raw, dict):
                    continue
                cargo = OzonFboCargo(
                    supply_id=int(supply_id),
                    cargo_number=_str(raw.get("cargo_number"), 64) or str(idx + 1),
                    ozon_cargo_id=_str(raw.get("ozon_cargo_id"), 64),
                    comment=_str(raw.get("comment"), 512),
                    sort_order=idx,
                )
                session.add(cargo)
                session.flush()
                for j, item in enumerate(raw.get("items") or []):
                    norm = self._normalize_item(item, j)
                    if norm is None:
                        continue
                    session.add(OzonFboCargoItem(cargo_id=int(cargo.id), **norm))
            supply.status = STATUS_PACKING if supply.status in {STATUS_DRAFT, STATUS_ASSIGNED} else supply.status
            supply.updated_at_ts = int(time.time())
            session.commit()
            session.refresh(supply)
            return self._row(session, supply, include_details=True)

    def assignees(self) -> list[dict[str, Any]]:
        rows = self.users_repo.list_users({"is_active": "1"})
        return [{"id": r.id, "display_name": r.display_name or r.login} for r in rows]

    def _replace_items(self, session: Session, supply_id: int, raw_items: list[dict[str, Any]]) -> None:
        session.execute(delete(OzonFboSupplyItem).where(OzonFboSupplyItem.supply_id == int(supply_id)))
        for idx, raw in enumerate(raw_items or []):
            norm = self._normalize_item(raw, idx)
            if norm is None:
                continue
            norm.pop("expiration_date", None)
            session.add(OzonFboSupplyItem(supply_id=int(supply_id), **norm))

    def _normalize_item(self, raw: Any, sort_order: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        product_id = _int_or_none(raw.get("product_id"))
        sku = _str(raw.get("sku"), 128)
        name = _str(raw.get("name"), 512)
        if product_id:
            product = self.catalog_repo.get_product(product_id)
            if product:
                sku = product.sku
                name = product.name
        if not sku:
            return None
        qty = _positive_int(raw.get("quantity"), default=0)
        if qty <= 0:
            return None
        return {
            "product_id": product_id,
            "sku": sku,
            "name": name,
            "quantity": qty,
            "expiration_date": _str(raw.get("expiration_date"), 10),
            "sort_order": sort_order,
        }

    def _validate_user_id(self, raw: Any) -> int | None:
        user_id = _int_or_none(raw)
        if user_id is None:
            return None
        if self.users_repo.get_by_id(user_id) is None:
            raise ValueError("Упаковщик не найден")
        return user_id

    def _row(self, session: Session, row: OzonFboSupply, *, include_details: bool) -> FboSupplyRow:
        assigned_name = ""
        if row.assigned_user_id:
            user = self.users_repo.get_by_id(int(row.assigned_user_id))
            if user:
                assigned_name = user.display_name or user.login
        manager_name = ""
        if row.manager_user_id:
            user = self.users_repo.get_by_id(int(row.manager_user_id))
            if user:
                manager_name = user.display_name or user.login

        items: list[FboSupplyItemRow] = []
        cargoes: list[FboCargoRow] = []
        if include_details:
            item_rows = session.scalars(
                select(OzonFboSupplyItem)
                .where(OzonFboSupplyItem.supply_id == int(row.id))
                .order_by(OzonFboSupplyItem.sort_order, OzonFboSupplyItem.id)
            ).all()
            items = [
                FboSupplyItemRow(
                    id=int(r.id),
                    product_id=int(r.product_id) if r.product_id is not None else None,
                    sku=str(r.sku),
                    name=str(r.name or ""),
                    quantity=int(r.quantity or 0),
                    sort_order=int(r.sort_order),
                )
                for r in item_rows
            ]
            cargo_rows = session.scalars(
                select(OzonFboCargo)
                .where(OzonFboCargo.supply_id == int(row.id))
                .order_by(OzonFboCargo.sort_order, OzonFboCargo.id)
            ).all()
            for cargo in cargo_rows:
                ci_rows = session.scalars(
                    select(OzonFboCargoItem)
                    .where(OzonFboCargoItem.cargo_id == int(cargo.id))
                    .order_by(OzonFboCargoItem.sort_order, OzonFboCargoItem.id)
                ).all()
                cargoes.append(
                    FboCargoRow(
                        id=int(cargo.id),
                        cargo_number=str(cargo.cargo_number or ""),
                        ozon_cargo_id=str(cargo.ozon_cargo_id or ""),
                        comment=str(cargo.comment or ""),
                        sort_order=int(cargo.sort_order),
                        items=[
                            FboCargoItemRow(
                                id=int(i.id),
                                product_id=int(i.product_id) if i.product_id is not None else None,
                                sku=str(i.sku),
                                name=str(i.name or ""),
                                quantity=int(i.quantity or 0),
                                expiration_date=str(i.expiration_date or ""),
                                sort_order=int(i.sort_order),
                            )
                            for i in ci_rows
                        ],
                    )
                )

        return FboSupplyRow(
            id=int(row.id),
            title=str(row.title or ""),
            supply_kind=str(row.supply_kind or SUPPLY_KIND_PALLET),
            status=str(row.status or STATUS_DRAFT),
            ozon_supply_id=str(row.ozon_supply_id or ""),
            ozon_draft_id=str(row.ozon_draft_id or ""),
            ozon_cluster_id=str(row.ozon_cluster_id or ""),
            ozon_cluster_name=str(row.ozon_cluster_name or ""),
            ozon_warehouse_id=str(row.ozon_warehouse_id or ""),
            ozon_warehouse_name=str(row.ozon_warehouse_name or ""),
            assigned_user_id=int(row.assigned_user_id) if row.assigned_user_id else None,
            assigned_user_name=assigned_name,
            manager_user_id=int(row.manager_user_id) if row.manager_user_id else None,
            manager_user_name=manager_name,
            cargoes_operation_id=str(row.cargoes_operation_id or ""),
            labels_operation_id=str(row.labels_operation_id or ""),
            labels_file_guid=str(row.labels_file_guid or ""),
            labels_filename=str(row.labels_filename or ""),
            comment=str(row.comment or ""),
            created_at_ts=int(row.created_at_ts or 0),
            updated_at_ts=int(row.updated_at_ts or 0),
            items=items,
            cargoes=cargoes,
        )


def supply_to_dict(row: FboSupplyRow, *, include_details: bool = True) -> dict[str, Any]:
    data = {
        "id": row.id,
        "title": row.title,
        "supply_kind": row.supply_kind,
        "supply_kind_label": "Паллеты" if row.supply_kind == SUPPLY_KIND_PALLET else "Короба",
        "status": row.status,
        "status_label": status_label(row.status),
        "ozon_supply_id": row.ozon_supply_id,
        "ozon_draft_id": row.ozon_draft_id,
        "ozon_cluster_id": row.ozon_cluster_id,
        "ozon_cluster_name": row.ozon_cluster_name,
        "ozon_warehouse_id": row.ozon_warehouse_id,
        "ozon_warehouse_name": row.ozon_warehouse_name,
        "assigned_user_id": row.assigned_user_id,
        "assigned_user_name": row.assigned_user_name,
        "manager_user_id": row.manager_user_id,
        "manager_user_name": row.manager_user_name,
        "cargoes_operation_id": row.cargoes_operation_id,
        "labels_operation_id": row.labels_operation_id,
        "labels_file_guid": row.labels_file_guid,
        "labels_filename": row.labels_filename,
        "comment": row.comment,
        "created_at_ts": row.created_at_ts,
        "updated_at_ts": row.updated_at_ts,
        "item_count": len(row.items),
        "cargo_count": len(row.cargoes),
    }
    if include_details:
        data["items"] = [
            {
                "id": i.id,
                "product_id": i.product_id,
                "sku": i.sku,
                "name": i.name,
                "quantity": i.quantity,
                "sort_order": i.sort_order,
            }
            for i in row.items
        ]
        data["cargoes"] = [
            {
                "id": c.id,
                "cargo_number": c.cargo_number,
                "ozon_cargo_id": c.ozon_cargo_id,
                "comment": c.comment,
                "sort_order": c.sort_order,
                "items": [
                    {
                        "id": i.id,
                        "product_id": i.product_id,
                        "sku": i.sku,
                        "name": i.name,
                        "quantity": i.quantity,
                        "expiration_date": i.expiration_date,
                        "sort_order": i.sort_order,
                    }
                    for i in c.items
                ],
            }
            for c in row.cargoes
        ]
    return data


def status_label(status: str) -> str:
    return {
        STATUS_DRAFT: "Черновик",
        STATUS_ASSIGNED: "Назначена",
        STATUS_PACKING: "Сборка",
        STATUS_READY: "Готова к Ozon",
        STATUS_SENT_TO_OZON: "Отправлена в Ozon",
        STATUS_LABELS_READY: "Этикетки готовы",
        STATUS_DONE: "Завершена",
    }.get(status, status)
