"""Локальные задания FBO-поставок Ozon для менеджера и упаковщиков."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, delete, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogRepository
from app.ozon_fbo_labels_storage import batch_labels_url, supply_labels_url
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


DELIVERY_DIRECT = "direct"
DELIVERY_CROSSDOCK = "crossdock"
DELIVERY_TYPES = {DELIVERY_DIRECT, DELIVERY_CROSSDOCK}

BATCH_STATUS_PLANNING = "planning"
BATCH_STATUS_SUBMITTED = "submitted"
BATCH_STATUS_PACKING = "packing"
BATCH_STATUS_DONE = "done"
BATCH_STATUSES = {BATCH_STATUS_PLANNING, BATCH_STATUS_SUBMITTED, BATCH_STATUS_PACKING, BATCH_STATUS_DONE}

_DEFAULT_PACKING_STATUSES = (
    ("В сборке", "#f9a825"),
    ("Готово", "#2e7d32"),
    ("Отгружено", "#1565c0"),
)

_DEFAULT_SUPPLY_TYPES: tuple[tuple[str, str, str], ...] = (
    (
        "Паллета",
        "#5c6bc0",
        "Уложить товары на паллету, обмотать стрейч-плёнкой, закрепить углами. Штрихкод на боковую грань.",
    ),
    (
        "Короб MIX",
        "#26a69a",
        "Смешанный короб: каждый товар в защиту, пустоты заполнить. Штрихкод на верхнюю грань.",
    ),
    (
        "Моно короб",
        "#8d6e63",
        "Один артикул в короб, количество по заявке. Штрихкод на торец короба.",
    ),
)


class _Base(DeclarativeBase):
    pass


class OzonFboBatch(_Base):
    __tablename__ = "ozon_fbo_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    delivery_type: Mapped[str] = mapped_column(String(16), nullable=False, default=DELIVERY_DIRECT)
    dropoff_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dropoff_warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    timeslot_from: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    timeslot_to: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=BATCH_STATUS_PLANNING)
    manager_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    ops_assembly_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    ops_cargoes_desc: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ops_packing_status_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ops_supply_type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ops_barcode_link_2: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    ops_packing_comment: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    ops_counterparty_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ops_weight_kg: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ops_unload_address_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ops_ship_time: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    ops_expense_doc_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ops_pallets_ready_time: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ops_logistics_comment: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    ops_car_driver: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboUnloadAddress(_Base):
    __tablename__ = "ozon_fbo_unload_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboPackingStatus(_Base):
    __tablename__ = "ozon_fbo_packing_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#9e9e9e")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OzonFboSupplyType(_Base):
    __tablename__ = "ozon_fbo_supply_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#9e9e9e")
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OzonFboSetting(_Base):
    __tablename__ = "ozon_fbo_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False, default="")


SETTING_DEFAULT_COUNTERPARTY_ID = "default_counterparty_id"


class OzonFboBatchPacker(_Base):
    __tablename__ = "ozon_fbo_batch_packers"

    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_batches.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class OzonFboSupply(_Base):
    __tablename__ = "ozon_fbo_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    supply_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=SUPPLY_KIND_PALLET)
    delivery_type: Mapped[str] = mapped_column(String(16), nullable=False, default=DELIVERY_DIRECT)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_DRAFT)
    ozon_supply_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_order_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_draft_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_bundle_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_cluster_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_cluster_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    ozon_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ozon_warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    dropoff_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dropoff_warehouse_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    timeslot_from: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    timeslot_to: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    assigned_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    manager_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cargoes_operation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_operation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_file_guid: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    labels_filename: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    labels_file: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboSupplyItem(_Base):
    __tablename__ = "ozon_fbo_supply_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_supplies.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    labels_file: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OzonFboCargoItem(_Base):
    __tablename__ = "ozon_fbo_cargo_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cargo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ozon_fbo_cargoes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    labels_file: str
    comment: str
    sort_order: int
    items: list[FboCargoItemRow] = field(default_factory=list)

@dataclass
class FboPackingStatusRow:
    id: int
    name: str
    color: str
    sort_order: int
    is_default: bool = False


@dataclass
class FboSupplyTypeRow:
    id: int
    name: str
    color: str
    comment: str
    sort_order: int
    is_default: bool = False


@dataclass
class FboUnloadAddressRow:
    id: int
    name: str
    address: str
    sort_order: int


@dataclass
class FboBatchRow:
    id: int
    title: str
    delivery_type: str
    dropoff_warehouse_id: str
    dropoff_warehouse_name: str
    timeslot_from: str
    timeslot_to: str
    status: str
    manager_user_id: int | None
    manager_user_name: str
    comment: str
    ops_assembly_date: str
    ops_cargoes_desc: str
    ops_packing_status_id: int | None
    ops_supply_type_id: int | None
    ops_barcode_link_2: str
    ops_packing_comment: str
    ops_counterparty_id: int | None
    ops_weight_kg: str
    ops_unload_address_id: int | None
    ops_ship_time: str
    ops_expense_doc_number: str
    ops_pallets_ready_time: str
    ops_logistics_comment: str
    ops_car_driver: str
    ops_packer_user_ids: list[int] = field(default_factory=list)
    ops_packer_display: str = ""
    created_at_ts: int
    updated_at_ts: int
    supply_count: int = 0
    labels_url: str = ""
    supplies: list["FboSupplyRow"] = field(default_factory=list)


@dataclass
class FboSupplyRow:
    id: int
    batch_id: int | None
    title: str
    supply_kind: str
    delivery_type: str
    status: str
    ozon_supply_id: str
    ozon_order_id: str
    ozon_draft_id: str
    ozon_bundle_id: str
    ozon_cluster_id: str
    ozon_cluster_name: str
    ozon_warehouse_id: str
    ozon_warehouse_name: str
    dropoff_warehouse_id: str
    dropoff_warehouse_name: str
    timeslot_from: str
    timeslot_to: str
    assigned_user_id: int | None
    assigned_user_name: str
    manager_user_id: int | None
    manager_user_name: str
    cargoes_operation_id: str
    labels_operation_id: str
    labels_file_guid: str
    labels_filename: str
    labels_file: str
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


def _normalize_delivery_type(raw: Any, *, default: str = DELIVERY_DIRECT) -> str:
    val = _str(raw, 16).lower()
    if not val:
        return default
    if val not in DELIVERY_TYPES:
        raise ValueError("Способ доставки: direct или crossdock")
    return val


def _normalize_batch_status(raw: Any, *, default: str = BATCH_STATUS_PLANNING) -> str:
    val = _str(raw, 32).lower()
    if not val:
        return default
    if val not in BATCH_STATUSES:
        raise ValueError("Некорректный статус пакета FBO")
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
        self._seed_packing_statuses()
        self._seed_supply_types()

    def _seed_supply_types(self) -> None:
        with Session(self.engine) as session:
            if session.scalar(select(func.count()).select_from(OzonFboSupplyType)):
                return
            for i, (name, color, comment) in enumerate(_DEFAULT_SUPPLY_TYPES):
                session.add(
                    OzonFboSupplyType(
                        name=name,
                        color=color,
                        comment=comment,
                        sort_order=i,
                        is_default=(i == 0),
                    )
                )
            session.commit()

    def _seed_packing_statuses(self) -> None:
        with Session(self.engine) as session:
            if session.scalar(select(func.count()).select_from(OzonFboPackingStatus)):
                return
            for i, (name, color) in enumerate(_DEFAULT_PACKING_STATUSES):
                session.add(
                    OzonFboPackingStatus(
                        name=name,
                        color=color,
                        sort_order=i,
                        is_default=(i == 0),
                    )
                )
            session.commit()

    def get_setting(self, key: str) -> str:
        with Session(self.engine) as session:
            row = session.get(OzonFboSetting, str(key))
            return str(row.value or "") if row is not None else ""

    def set_setting(self, key: str, value: str | None) -> None:
        key = str(key)
        with Session(self.engine) as session:
            if value is None or not str(value).strip():
                session.execute(delete(OzonFboSetting).where(OzonFboSetting.key == key))
            else:
                row = session.get(OzonFboSetting, key)
                if row is None:
                    session.add(OzonFboSetting(key=key, value=str(value).strip()))
                else:
                    row.value = str(value).strip()
            session.commit()

    def default_counterparty_id(self) -> int | None:
        return _int_or_none(self.get_setting(SETTING_DEFAULT_COUNTERPARTY_ID))

    def set_default_counterparty_id(
        self,
        counterparty_id: int | None,
        *,
        apply_to_empty: bool = True,
    ) -> dict[str, Any]:
        now = int(time.time())
        updated = 0
        with Session(self.engine) as session:
            if counterparty_id:
                row = session.get(OzonFboSetting, SETTING_DEFAULT_COUNTERPARTY_ID)
                value = str(int(counterparty_id))
                if row is None:
                    session.add(OzonFboSetting(key=SETTING_DEFAULT_COUNTERPARTY_ID, value=value))
                else:
                    row.value = value
            else:
                session.execute(
                    delete(OzonFboSetting).where(OzonFboSetting.key == SETTING_DEFAULT_COUNTERPARTY_ID)
                )
            if apply_to_empty and counterparty_id:
                batches = session.scalars(
                    select(OzonFboBatch).where(OzonFboBatch.ops_counterparty_id.is_(None))
                ).all()
                for batch in batches:
                    batch.ops_counterparty_id = int(counterparty_id)
                    batch.updated_at_ts = now
                    updated += 1
            session.commit()
        return {
            "default_counterparty_id": int(counterparty_id) if counterparty_id else None,
            "updated_batches": updated,
        }

    def effective_counterparty_id(self, batch: FboBatchRow) -> int | None:
        cid = int(batch.ops_counterparty_id or 0) or None
        if cid:
            return cid
        return self.default_counterparty_id()

    def _default_counterparty_id_session(self, session: Session) -> int | None:
        row = session.get(OzonFboSetting, SETTING_DEFAULT_COUNTERPARTY_ID)
        if row is None:
            return None
        return _int_or_none(row.value)

    def _default_supply_type_id_session(self, session: Session) -> int | None:
        row = session.scalar(
            select(OzonFboSupplyType.id)
            .where(OzonFboSupplyType.is_default.is_(True))
            .order_by(OzonFboSupplyType.sort_order, OzonFboSupplyType.id)
            .limit(1)
        )
        return int(row) if row else None

    def _apply_new_batch_defaults(self, session: Session, row: OzonFboBatch) -> None:
        if row.ops_counterparty_id is None:
            default_cp = self._default_counterparty_id_session(session)
            if default_cp:
                row.ops_counterparty_id = default_cp
        if row.ops_supply_type_id is None:
            default_type = self._default_supply_type_id_session(session)
            if default_type:
                row.ops_supply_type_id = default_type

    def _ensure_columns(self) -> None:
        with self.engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(ozon_fbo_supplies)")).all()
            }
            migrations = (
                ("cargoes_operation_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
                ("labels_operation_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
                ("batch_id", "INTEGER"),
                ("delivery_type", "VARCHAR(16) NOT NULL DEFAULT 'direct'"),
                ("ozon_bundle_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("ozon_order_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("dropoff_warehouse_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("dropoff_warehouse_name", "VARCHAR(256) NOT NULL DEFAULT ''"),
                ("timeslot_from", "VARCHAR(32) NOT NULL DEFAULT ''"),
                ("timeslot_to", "VARCHAR(32) NOT NULL DEFAULT ''"),
            )
            for name, ddl in migrations:
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE ozon_fbo_supplies ADD COLUMN {name} {ddl}"))
            cargo_cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(ozon_fbo_cargoes)")).all()
            }
            if "labels_file" not in cargo_cols:
                conn.execute(
                    text("ALTER TABLE ozon_fbo_cargoes ADD COLUMN labels_file VARCHAR(256) NOT NULL DEFAULT ''")
                )
            if "labels_file" not in cols:
                conn.execute(
                    text("ALTER TABLE ozon_fbo_supplies ADD COLUMN labels_file VARCHAR(256) NOT NULL DEFAULT ''")
                )
            ops_cols = {
                "ops_assembly_date": "VARCHAR(10) NOT NULL DEFAULT ''",
                "ops_ship_date": "VARCHAR(10) NOT NULL DEFAULT ''",
                "ops_movement_number": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_units_count": "VARCHAR(32) NOT NULL DEFAULT ''",
                "ops_cargoes_desc": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_packing_status": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_barcode_link": "VARCHAR(512) NOT NULL DEFAULT ''",
                "ops_barcode_link_2": "VARCHAR(512) NOT NULL DEFAULT ''",
                "ops_packing_comment": "VARCHAR(1024) NOT NULL DEFAULT ''",
                "ops_client": "VARCHAR(64) NOT NULL DEFAULT 'OZON'",
                "ops_weight_kg": "VARCHAR(32) NOT NULL DEFAULT ''",
                "ops_unload_address": "VARCHAR(256) NOT NULL DEFAULT ''",
                "ops_ship_time": "VARCHAR(16) NOT NULL DEFAULT ''",
                "ops_expense_doc_number": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_pallets_ready_time": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_logistics_comment": "VARCHAR(1024) NOT NULL DEFAULT ''",
                "ops_car_driver": "VARCHAR(256) NOT NULL DEFAULT ''",
            }
            for name, ddl in ops_cols.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE ozon_fbo_supplies ADD COLUMN {name} {ddl}"))
            batch_cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(ozon_fbo_batches)")).all()
            }
            batch_ops_cols = {
                "ops_assembly_date": "VARCHAR(10) NOT NULL DEFAULT ''",
                "ops_cargoes_desc": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_packing_status_id": "INTEGER",
                "ops_supply_type_id": "INTEGER",
                "ops_barcode_link_2": "VARCHAR(512) NOT NULL DEFAULT ''",
                "ops_packing_comment": "VARCHAR(1024) NOT NULL DEFAULT ''",
                "ops_counterparty_id": "INTEGER",
                "ops_weight_kg": "VARCHAR(32) NOT NULL DEFAULT ''",
                "ops_unload_address_id": "INTEGER",
                "ops_ship_time": "VARCHAR(16) NOT NULL DEFAULT ''",
                "ops_expense_doc_number": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_pallets_ready_time": "VARCHAR(64) NOT NULL DEFAULT ''",
                "ops_logistics_comment": "VARCHAR(1024) NOT NULL DEFAULT ''",
                "ops_car_driver": "VARCHAR(256) NOT NULL DEFAULT ''",
            }
            for name, ddl in batch_ops_cols.items():
                if name not in batch_cols:
                    conn.execute(text(f"ALTER TABLE ozon_fbo_batches ADD COLUMN {name} {ddl}"))

    def list_packing_statuses(self) -> list[FboPackingStatusRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(OzonFboPackingStatus).order_by(
                    OzonFboPackingStatus.sort_order, OzonFboPackingStatus.name
                )
            ).all()
            return [
                FboPackingStatusRow(
                    id=int(r.id),
                    name=str(r.name or ""),
                    color=str(r.color or "#9e9e9e"),
                    sort_order=int(r.sort_order or 0),
                    is_default=bool(r.is_default),
                )
                for r in rows
            ]

    def packing_status_name(self, status_id: int | None) -> str:
        if not status_id:
            return ""
        for row in self.list_packing_statuses():
            if int(row.id) == int(status_id):
                return row.name
        return ""

    def save_packing_statuses(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {
                int(r.id): r for r in session.scalars(select(OzonFboPackingStatus)).all()
            }
            keep_ids: set[int] = set()
            for idx, raw in enumerate(items or []):
                if not isinstance(raw, dict):
                    continue
                name = _str(raw.get("name"), 128)
                if not name:
                    continue
                color = _str(raw.get("color") or "#9e9e9e", 16) or "#9e9e9e"
                row = None
                raw_id = _int_or_none(raw.get("id"))
                if raw_id and raw_id in existing:
                    row = existing[raw_id]
                if row is None:
                    row = OzonFboPackingStatus()
                    session.add(row)
                row.name = name
                row.color = color
                row.sort_order = idx
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids and not row.is_default:
                    session.delete(row)
            session.commit()
        return [
            {
                "id": r.id,
                "name": r.name,
                "color": r.color,
                "sort_order": r.sort_order,
                "is_default": r.is_default,
            }
            for r in self.list_packing_statuses()
        ]

    def list_supply_types(self) -> list[FboSupplyTypeRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(OzonFboSupplyType).order_by(
                    OzonFboSupplyType.sort_order, OzonFboSupplyType.name
                )
            ).all()
            return [
                FboSupplyTypeRow(
                    id=int(r.id),
                    name=str(r.name or ""),
                    color=str(r.color or "#9e9e9e"),
                    comment=str(r.comment or ""),
                    sort_order=int(r.sort_order or 0),
                    is_default=bool(r.is_default),
                )
                for r in rows
            ]

    def supply_type_name(self, type_id: int | None) -> str:
        if not type_id:
            return ""
        for row in self.list_supply_types():
            if int(row.id) == int(type_id):
                return row.name
        return ""

    def save_supply_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {int(r.id): r for r in session.scalars(select(OzonFboSupplyType)).all()}
            keep_ids: set[int] = set()
            for idx, raw in enumerate(items or []):
                if not isinstance(raw, dict):
                    continue
                name = _str(raw.get("name"), 128)
                if not name:
                    continue
                color = _str(raw.get("color") or "#9e9e9e", 16) or "#9e9e9e"
                comment = _str(raw.get("comment"), 2048)
                row = None
                raw_id = _int_or_none(raw.get("id"))
                if raw_id and raw_id in existing:
                    row = existing[raw_id]
                if row is None:
                    row = OzonFboSupplyType()
                    session.add(row)
                row.name = name
                row.color = color
                row.comment = comment
                row.sort_order = idx
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids and not row.is_default:
                    session.delete(row)
            session.commit()
        return [
            {
                "id": r.id,
                "name": r.name,
                "color": r.color,
                "comment": r.comment,
                "sort_order": r.sort_order,
                "is_default": r.is_default,
            }
            for r in self.list_supply_types()
        ]

    def list_unload_addresses(self) -> list[FboUnloadAddressRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(OzonFboUnloadAddress).order_by(OzonFboUnloadAddress.sort_order, OzonFboUnloadAddress.id)
            ).all()
            return [
                FboUnloadAddressRow(
                    id=int(r.id),
                    name=str(r.name or ""),
                    address=str(r.address or ""),
                    sort_order=int(r.sort_order or 0),
                )
                for r in rows
            ]

    def unload_addresses_map(self) -> dict[int, str]:
        return {row.id: row.address for row in self.list_unload_addresses()}

    def save_unload_addresses(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {int(r.id): r for r in session.scalars(select(OzonFboUnloadAddress)).all()}
            keep_ids: set[int] = set()
            for idx, raw in enumerate(items or []):
                if not isinstance(raw, dict):
                    continue
                name = _str(raw.get("name"), 128)
                address = _str(raw.get("address"), 512)
                if not name:
                    continue
                row = None
                raw_id = _int_or_none(raw.get("id"))
                if raw_id and raw_id in existing:
                    row = existing[raw_id]
                if row is None:
                    row = OzonFboUnloadAddress()
                    session.add(row)
                row.name = name
                row.address = address
                row.sort_order = idx
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids:
                    session.delete(row)
            session.commit()
        rows = self.list_unload_addresses()
        return [
            {"id": r.id, "name": r.name, "address": r.address, "sort_order": r.sort_order}
            for r in rows
        ]

    def _normalize_user_ids(self, raw: Any) -> list[int]:
        if not isinstance(raw, list):
            return []
        out: list[int] = []
        seen: set[int] = set()
        for item in raw:
            uid = _int_or_none(item)
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
        return sorted(out)

    def _batch_packer_ids_session(self, session: Session, batch_id: int) -> list[int]:
        rows = session.scalars(
            select(OzonFboBatchPacker.user_id)
            .where(OzonFboBatchPacker.batch_id == int(batch_id))
            .order_by(OzonFboBatchPacker.user_id)
        ).all()
        return [int(r) for r in rows]

    def _fallback_packer_ids_from_supplies(self, supplies: list[FboSupplyRow]) -> list[int]:
        seen: set[int] = set()
        out: list[int] = []
        for supply in supplies:
            uid = int(supply.assigned_user_id or 0)
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
        return out

    def _packer_names(self, user_ids: list[int]) -> str:
        if not user_ids:
            return ""
        names: list[str] = []
        for uid in user_ids:
            user = self.users_repo.get_by_id(int(uid))
            if user:
                names.append(str(user.display_name or user.login or "").strip())
        return ", ".join([n for n in names if n])

    def _set_batch_packers(self, session: Session, batch_id: int, user_ids: list[int]) -> None:
        session.execute(delete(OzonFboBatchPacker).where(OzonFboBatchPacker.batch_id == int(batch_id)))
        for uid in user_ids:
            if self.users_repo.get_by_id(int(uid)) is None:
                continue
            session.add(OzonFboBatchPacker(batch_id=int(batch_id), user_id=int(uid)))

    def _apply_batch_ops_fields(self, row: OzonFboBatch, data: dict[str, Any]) -> None:
        from app.ozon_fbo_ops_sheets import batch_ops_editable_field_names

        ops_payload = data.get("ops") if isinstance(data.get("ops"), dict) else data
        field_names = batch_ops_editable_field_names()
        for key in field_names:
            if key not in ops_payload:
                continue
            if key.endswith("_id"):
                setattr(row, key, _int_or_none(ops_payload.get(key)))
                continue
            limit = 1024 if "comment" in key else 512 if "link" in key else 256 if key == "ops_car_driver" else 64
            if key in {"ops_assembly_date"}:
                limit = 10
            if key == "ops_weight_kg":
                limit = 32
            if key == "ops_ship_time":
                limit = 16
            setattr(row, key, _str(ops_payload.get(key), limit))

    def list_batches(self, filters: dict[str, str] | None = None) -> list[FboBatchRow]:
        filters = filters or {}
        with Session(self.engine) as session:
            q = select(OzonFboBatch).order_by(OzonFboBatch.updated_at_ts.desc(), OzonFboBatch.id.desc())
            status = _str(filters.get("status"), 32)
            if status:
                q = q.where(OzonFboBatch.status == status)
            rows = session.scalars(q.limit(200)).all()
            return [self._batch_row(session, r, include_details=False) for r in rows]

    def get_batch(self, batch_id: int) -> FboBatchRow | None:
        with Session(self.engine) as session:
            row = session.get(OzonFboBatch, int(batch_id))
            if row is None:
                return None
            return self._batch_row(session, row, include_details=True)

    def create_batch(self, data: dict[str, Any], *, manager_user_id: int | None = None) -> FboBatchRow:
        now = int(time.time())
        with Session(self.engine) as session:
            row = OzonFboBatch(
                title=_str(data.get("title"), 256) or "Пакет FBO",
                delivery_type=_normalize_delivery_type(data.get("delivery_type")),
                dropoff_warehouse_id=_str(data.get("dropoff_warehouse_id"), 64),
                dropoff_warehouse_name=_str(data.get("dropoff_warehouse_name"), 256),
                timeslot_from=_str(data.get("timeslot_from"), 32),
                timeslot_to=_str(data.get("timeslot_to"), 32),
                status=_normalize_batch_status(data.get("status")),
                manager_user_id=int(manager_user_id) if manager_user_id else None,
                comment=_str(data.get("comment"), 2048),
                created_at_ts=now,
                updated_at_ts=now,
            )
            session.add(row)
            session.flush()
            self._apply_new_batch_defaults(session, row)
            session.commit()
            session.refresh(row)
            return self._batch_row(session, row, include_details=True)

    def update_batch(self, batch_id: int, data: dict[str, Any]) -> FboBatchRow | None:
        now = int(time.time())
        with Session(self.engine) as session:
            row = session.get(OzonFboBatch, int(batch_id))
            if row is None:
                return None
            for key, limit in (
                ("title", 256),
                ("dropoff_warehouse_id", 64),
                ("dropoff_warehouse_name", 256),
                ("timeslot_from", 32),
                ("timeslot_to", 32),
                ("comment", 2048),
            ):
                if key in data:
                    setattr(row, key, _str(data.get(key), limit))
            if "delivery_type" in data:
                row.delivery_type = _normalize_delivery_type(data.get("delivery_type"))
            if "status" in data:
                row.status = _normalize_batch_status(data.get("status"), default=row.status)
            ops_payload = data.get("ops") if isinstance(data.get("ops"), dict) else data
            if isinstance(ops_payload, dict) and "ops_packer_user_ids" in ops_payload:
                self._set_batch_packers(
                    session,
                    int(row.id),
                    self._normalize_user_ids(ops_payload.get("ops_packer_user_ids")),
                )
            if "ops" in data or any(k.startswith("ops_") for k in data):
                self._apply_batch_ops_fields(row, data)
            row.updated_at_ts = now
            session.commit()
            session.refresh(row)
            return self._batch_row(session, row, include_details=True)

    def list_supplies_for_batch(self, batch_id: int) -> list[FboSupplyRow]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(OzonFboSupply)
                .where(OzonFboSupply.batch_id == int(batch_id))
                .order_by(OzonFboSupply.id)
            ).all()
            return [self._row(session, r, include_details=True) for r in rows]

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
            batch_id = _int_or_none(filters.get("batch_id"))
            if batch_id:
                conds.append(OzonFboSupply.batch_id == batch_id)
            q_text = _str(filters.get("q"), 128)
            if q_text:
                pat = f"%{q_text}%"
                from sqlalchemy import or_

                conds.append(
                    or_(
                        OzonFboSupply.title.ilike(pat),
                        OzonFboSupply.ozon_supply_id.ilike(pat),
                        OzonFboSupply.ozon_order_id.ilike(pat),
                        OzonFboSupply.ozon_cluster_name.ilike(pat),
                        OzonFboSupply.ozon_warehouse_name.ilike(pat),
                    )
                )
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q.limit(500)).all()
            return [self._row(session, r, include_details=False) for r in rows]

    def catalog_map_for_supplies(self, supplies: list[FboSupplyRow]) -> dict[str, dict[str, Any]]:
        skus: list[str] = []
        for supply in supplies:
            for item in supply.items:
                skus.append(item.sku)
            for cargo in supply.cargoes:
                for item in cargo.items:
                    skus.append(item.sku)
        return self.catalog_repo.lookup_products_by_skus(skus)

    def catalog_map_for_supply(self, supply: FboSupplyRow) -> dict[str, dict[str, Any]]:
        return self.catalog_map_for_supplies([supply])

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
                batch_id=_int_or_none(data.get("batch_id")),
                title=title,
                supply_kind=_normalize_supply_kind(data.get("supply_kind") or SUPPLY_KIND_PALLET),
                delivery_type=_normalize_delivery_type(data.get("delivery_type")),
                status=_normalize_status(data.get("status"), default=STATUS_DRAFT),
                ozon_supply_id=_str(data.get("ozon_supply_id"), 64),
                ozon_order_id=_str(data.get("ozon_order_id"), 64),
                ozon_draft_id=_str(data.get("ozon_draft_id"), 64),
                ozon_bundle_id=_str(data.get("ozon_bundle_id"), 64),
                ozon_cluster_id=_str(data.get("ozon_cluster_id"), 64),
                ozon_cluster_name=_str(data.get("ozon_cluster_name"), 256),
                ozon_warehouse_id=_str(data.get("ozon_warehouse_id"), 64),
                ozon_warehouse_name=_str(data.get("ozon_warehouse_name"), 256),
                dropoff_warehouse_id=_str(data.get("dropoff_warehouse_id"), 64),
                dropoff_warehouse_name=_str(data.get("dropoff_warehouse_name"), 256),
                timeslot_from=_str(data.get("timeslot_from"), 32),
                timeslot_to=_str(data.get("timeslot_to"), 32),
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
                ("ozon_order_id", 64),
                ("ozon_draft_id", 64),
                ("ozon_bundle_id", 64),
                ("ozon_cluster_id", 64),
                ("ozon_cluster_name", 256),
                ("ozon_warehouse_id", 64),
                ("ozon_warehouse_name", 256),
                ("dropoff_warehouse_id", 64),
                ("dropoff_warehouse_name", 256),
                ("timeslot_from", 32),
                ("timeslot_to", 32),
                ("cargoes_operation_id", 128),
                ("labels_operation_id", 128),
                ("labels_file_guid", 128),
                ("labels_filename", 256),
                ("labels_file", 256),
                ("comment", 2048),
            ):
                if key in data:
                    setattr(row, key, _str(data.get(key), limit))
            if "batch_id" in data:
                row.batch_id = _int_or_none(data.get("batch_id"))
            if "supply_kind" in data:
                row.supply_kind = _normalize_supply_kind(data.get("supply_kind"))
            if "delivery_type" in data:
                row.delivery_type = _normalize_delivery_type(data.get("delivery_type"))
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

    def get_cargo(self, cargo_id: int) -> FboCargoRow | None:
        with Session(self.engine) as session:
            row = session.get(OzonFboCargo, int(cargo_id))
            if row is None:
                return None
            return self._cargo_row(session, row)

    def set_supply_labels_file(self, supply_id: int, labels_file: str) -> None:
        from app.ozon_fbo_labels_storage import delete_supply_label

        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return
            old = str(row.labels_file or "").strip()
            new = str(labels_file or "").strip()
            if old and old != new:
                delete_supply_label(old)
            row.labels_file = new[:256]
            session.commit()

    def clear_supply_labels(self, supply_id: int) -> None:
        from app.ozon_fbo_labels_storage import delete_supply_label, delete_supply_labels

        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return
            old = str(row.labels_file or "").strip()
            if old:
                delete_supply_label(old)
            row.labels_file = ""
            session.commit()
        delete_supply_labels(supply_id)

    def delete_supply(self, supply_id: int) -> bool:
        self.clear_supply_labels(supply_id)
        with Session(self.engine) as session:
            row = session.get(OzonFboSupply, int(supply_id))
            if row is None:
                return False
            cargo_ids = [
                int(r.id)
                for r in session.scalars(
                    select(OzonFboCargo).where(OzonFboCargo.supply_id == int(supply_id))
                ).all()
            ]
            if cargo_ids:
                session.execute(delete(OzonFboCargoItem).where(OzonFboCargoItem.cargo_id.in_(cargo_ids)))
            session.execute(delete(OzonFboCargo).where(OzonFboCargo.supply_id == int(supply_id)))
            session.execute(delete(OzonFboSupplyItem).where(OzonFboSupplyItem.supply_id == int(supply_id)))
            session.delete(row)
            session.commit()
            return True

    def delete_batch(self, batch_id: int) -> bool:
        with Session(self.engine) as session:
            batch = session.get(OzonFboBatch, int(batch_id))
            if batch is None:
                return False
            supply_ids = [
                int(r.id)
                for r in session.scalars(
                    select(OzonFboSupply).where(OzonFboSupply.batch_id == int(batch_id))
                ).all()
            ]
        for sid in supply_ids:
            self.delete_supply(sid)
        with Session(self.engine) as session:
            batch = session.get(OzonFboBatch, int(batch_id))
            if batch is None:
                return True
            session.delete(batch)
            session.commit()
            return True

    def save_cargoes(self, supply_id: int, cargoes: list[dict[str, Any]]) -> FboSupplyRow | None:
        self.clear_supply_labels(supply_id)
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

    def apply_ozon_cargo_ids(self, supply_id: int, create_info: dict[str, Any]) -> FboSupplyRow | None:
        result = create_info.get("result") or {}
        key_to_id: dict[str, str] = {}
        for entry in result.get("cargoes") or []:
            key = str(entry.get("key") or "").strip()
            cargo_id = str((entry.get("value") or {}).get("cargo_id") or "").strip()
            if key and cargo_id:
                key_to_id[key] = cargo_id
        if not key_to_id:
            return self.get_supply(supply_id)
        from app.ozon_fbo_api import cargo_api_key

        with Session(self.engine) as session:
            supply = session.get(OzonFboSupply, int(supply_id))
            if supply is None:
                return None
            cargo_rows = session.scalars(
                select(OzonFboCargo)
                .where(OzonFboCargo.supply_id == int(supply_id))
                .order_by(OzonFboCargo.sort_order, OzonFboCargo.id)
            ).all()
            for idx, cargo_row in enumerate(cargo_rows):
                key = cargo_api_key(
                    {"cargo_number": cargo_row.cargo_number},
                    idx,
                    supply_id=int(supply_id),
                )
                ozon_id = key_to_id.get(key)
                if ozon_id:
                    cargo_row.ozon_cargo_id = ozon_id
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

    def _cargo_row(self, session: Session, cargo: OzonFboCargo) -> FboCargoRow:
        ci_rows = session.scalars(
            select(OzonFboCargoItem)
            .where(OzonFboCargoItem.cargo_id == int(cargo.id))
            .order_by(OzonFboCargoItem.sort_order, OzonFboCargoItem.id)
        ).all()
        return FboCargoRow(
            id=int(cargo.id),
            cargo_number=str(cargo.cargo_number or ""),
            ozon_cargo_id=str(cargo.ozon_cargo_id or ""),
            labels_file=str(cargo.labels_file or ""),
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
            cargoes = [self._cargo_row(session, cargo) for cargo in cargo_rows]

        return FboSupplyRow(
            id=int(row.id),
            batch_id=int(row.batch_id) if row.batch_id is not None else None,
            title=str(row.title or ""),
            supply_kind=str(row.supply_kind or SUPPLY_KIND_PALLET),
            delivery_type=str(row.delivery_type or DELIVERY_DIRECT),
            status=str(row.status or STATUS_DRAFT),
            ozon_supply_id=str(row.ozon_supply_id or ""),
            ozon_order_id=str(row.ozon_order_id or ""),
            ozon_draft_id=str(row.ozon_draft_id or ""),
            ozon_bundle_id=str(row.ozon_bundle_id or ""),
            ozon_cluster_id=str(row.ozon_cluster_id or ""),
            ozon_cluster_name=str(row.ozon_cluster_name or ""),
            ozon_warehouse_id=str(row.ozon_warehouse_id or ""),
            ozon_warehouse_name=str(row.ozon_warehouse_name or ""),
            dropoff_warehouse_id=str(row.dropoff_warehouse_id or ""),
            dropoff_warehouse_name=str(row.dropoff_warehouse_name or ""),
            timeslot_from=str(row.timeslot_from or ""),
            timeslot_to=str(row.timeslot_to or ""),
            assigned_user_id=int(row.assigned_user_id) if row.assigned_user_id else None,
            assigned_user_name=assigned_name,
            manager_user_id=int(row.manager_user_id) if row.manager_user_id else None,
            manager_user_name=manager_name,
            cargoes_operation_id=str(row.cargoes_operation_id or ""),
            labels_operation_id=str(row.labels_operation_id or ""),
            labels_file_guid=str(row.labels_file_guid or ""),
            labels_filename=str(row.labels_filename or ""),
            labels_file=str(row.labels_file or ""),
            comment=str(row.comment or ""),
            created_at_ts=int(row.created_at_ts or 0),
            updated_at_ts=int(row.updated_at_ts or 0),
            items=items,
            cargoes=cargoes,
        )

    def _batch_row(self, session: Session, row: OzonFboBatch, *, include_details: bool) -> FboBatchRow:
        manager_name = ""
        if row.manager_user_id:
            user = self.users_repo.get_by_id(int(row.manager_user_id))
            if user:
                manager_name = user.display_name or user.login
        supply_count = session.scalar(
            select(func.count())
            .select_from(OzonFboSupply)
            .where(OzonFboSupply.batch_id == int(row.id))
        )
        labels_count = session.scalar(
            select(func.count())
            .select_from(OzonFboSupply)
            .where(OzonFboSupply.batch_id == int(row.id))
            .where(OzonFboSupply.labels_file != "")
        )
        supplies: list[FboSupplyRow] = []
        if include_details:
            supply_rows = session.scalars(
                select(OzonFboSupply)
                .where(OzonFboSupply.batch_id == int(row.id))
                .order_by(OzonFboSupply.id)
            ).all()
            supplies = [self._row(session, s, include_details=True) for s in supply_rows]
        packer_ids = self._batch_packer_ids_session(session, int(row.id))
        if not packer_ids and supplies:
            packer_ids = self._fallback_packer_ids_from_supplies(supplies)
        packer_display = self._packer_names(packer_ids)
        return FboBatchRow(
            id=int(row.id),
            title=str(row.title or ""),
            delivery_type=str(row.delivery_type or DELIVERY_DIRECT),
            dropoff_warehouse_id=str(row.dropoff_warehouse_id or ""),
            dropoff_warehouse_name=str(row.dropoff_warehouse_name or ""),
            timeslot_from=str(row.timeslot_from or ""),
            timeslot_to=str(row.timeslot_to or ""),
            status=str(row.status or BATCH_STATUS_PLANNING),
            manager_user_id=int(row.manager_user_id) if row.manager_user_id else None,
            manager_user_name=manager_name,
            comment=str(row.comment or ""),
            ops_assembly_date=str(row.ops_assembly_date or ""),
            ops_cargoes_desc=str(row.ops_cargoes_desc or ""),
            ops_packing_status_id=int(row.ops_packing_status_id) if row.ops_packing_status_id else None,
            ops_supply_type_id=int(row.ops_supply_type_id) if row.ops_supply_type_id else None,
            ops_barcode_link_2=str(row.ops_barcode_link_2 or ""),
            ops_packing_comment=str(row.ops_packing_comment or ""),
            ops_counterparty_id=int(row.ops_counterparty_id) if row.ops_counterparty_id else None,
            ops_weight_kg=str(row.ops_weight_kg or ""),
            ops_unload_address_id=int(row.ops_unload_address_id) if row.ops_unload_address_id else None,
            ops_ship_time=str(row.ops_ship_time or ""),
            ops_expense_doc_number=str(row.ops_expense_doc_number or ""),
            ops_pallets_ready_time=str(row.ops_pallets_ready_time or ""),
            ops_logistics_comment=str(row.ops_logistics_comment or ""),
            ops_car_driver=str(row.ops_car_driver or ""),
            ops_packer_user_ids=packer_ids,
            ops_packer_display=packer_display,
            created_at_ts=int(row.created_at_ts or 0),
            updated_at_ts=int(row.updated_at_ts or 0),
            supply_count=int(supply_count or 0),
            labels_url=batch_labels_url(int(row.id)) if int(labels_count or 0) > 0 else "",
            supplies=supplies,
        )


def supply_to_dict(
    row: FboSupplyRow,
    *,
    include_details: bool = True,
    catalog_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data = {
        "id": row.id,
        "batch_id": row.batch_id,
        "title": row.title,
        "supply_kind": row.supply_kind,
        "supply_kind_label": "Паллеты" if row.supply_kind == SUPPLY_KIND_PALLET else "Короба",
        "delivery_type": row.delivery_type,
        "delivery_type_label": delivery_type_label(row.delivery_type),
        "status": row.status,
        "status_label": status_label(row.status),
        "ozon_supply_id": row.ozon_supply_id,
        "ozon_order_id": row.ozon_order_id,
        "ozon_draft_id": row.ozon_draft_id,
        "ozon_bundle_id": row.ozon_bundle_id,
        "ozon_cluster_id": row.ozon_cluster_id,
        "ozon_cluster_name": row.ozon_cluster_name,
        "ozon_warehouse_id": row.ozon_warehouse_id,
        "ozon_warehouse_name": row.ozon_warehouse_name,
        "dropoff_warehouse_id": row.dropoff_warehouse_id,
        "dropoff_warehouse_name": row.dropoff_warehouse_name,
        "timeslot_from": row.timeslot_from,
        "timeslot_to": row.timeslot_to,
        "timeslot_label": format_timeslot(row.timeslot_from, row.timeslot_to),
        "assigned_user_id": row.assigned_user_id,
        "assigned_user_name": row.assigned_user_name,
        "manager_user_id": row.manager_user_id,
        "manager_user_name": row.manager_user_name,
        "cargoes_operation_id": row.cargoes_operation_id,
        "labels_operation_id": row.labels_operation_id,
        "labels_file_guid": row.labels_file_guid,
        "labels_filename": row.labels_filename,
        "labels_url": supply_labels_url(row.id) if str(row.labels_file or "").strip() else "",
        "comment": row.comment,
        "created_at_ts": row.created_at_ts,
        "updated_at_ts": row.updated_at_ts,
        "item_count": len(row.items),
        "cargo_count": len(row.cargoes),
    }
    if include_details:
        data["items"] = [
            _supply_item_dict(i, catalog_map)
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
                    _cargo_item_dict(i, catalog_map)
                    for i in c.items
                ],
            }
            for c in row.cargoes
        ]
    return data


def _catalog_entry(catalog_map: dict[str, dict[str, Any]] | None, sku: str) -> dict[str, Any] | None:
    if not catalog_map:
        return None
    return catalog_map.get(str(sku or "").strip().casefold())


def _supply_item_dict(item: FboSupplyItemRow, catalog_map: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    cat = _catalog_entry(catalog_map, item.sku)
    return {
        "id": item.id,
        "product_id": item.product_id or (cat["id"] if cat else None),
        "sku": item.sku,
        "name": item.name or (cat["name"] if cat else ""),
        "quantity": item.quantity,
        "sort_order": item.sort_order,
        "image_url": (cat["image_url"] if cat else "") or "",
    }


def _cargo_item_dict(item: FboCargoItemRow, catalog_map: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    cat = _catalog_entry(catalog_map, item.sku)
    return {
        "id": item.id,
        "product_id": item.product_id or (cat["id"] if cat else None),
        "sku": item.sku,
        "name": item.name or (cat["name"] if cat else ""),
        "quantity": item.quantity,
        "expiration_date": item.expiration_date,
        "sort_order": item.sort_order,
        "image_url": (cat["image_url"] if cat else "") or "",
    }


def batch_to_dict(
    row: FboBatchRow,
    *,
    include_details: bool = True,
    catalog_map: dict[str, dict[str, Any]] | None = None,
    counterparty_name: str = "",
    unload_address: str = "",
    packing_status_name: str = "",
    supply_type_name: str = "",
    cluster_name_map: dict[str, str] | None = None,
    default_counterparty_id: int | None = None,
) -> dict[str, Any]:
    effective_cp_id = row.ops_counterparty_id or default_counterparty_id
    data = {
        "id": row.id,
        "title": row.title,
        "delivery_type": row.delivery_type,
        "delivery_type_label": delivery_type_label(row.delivery_type),
        "dropoff_warehouse_id": row.dropoff_warehouse_id,
        "dropoff_warehouse_name": row.dropoff_warehouse_name,
        "timeslot_from": row.timeslot_from,
        "timeslot_to": row.timeslot_to,
        "timeslot_label": format_timeslot(row.timeslot_from, row.timeslot_to),
        "status": row.status,
        "status_label": batch_status_label(row.status),
        "manager_user_id": row.manager_user_id,
        "manager_user_name": row.manager_user_name,
        "comment": row.comment,
        "ops_assembly_date": row.ops_assembly_date,
        "ops_cargoes_desc": row.ops_cargoes_desc,
        "ops_packing_status_id": row.ops_packing_status_id,
        "ops_packing_status_name": packing_status_name,
        "ops_supply_type_id": row.ops_supply_type_id,
        "ops_supply_type_name": supply_type_name,
        "ops_barcode_link_2": row.ops_barcode_link_2,
        "ops_packing_comment": row.ops_packing_comment,
        "ops_counterparty_id": effective_cp_id,
        "ops_counterparty_name": counterparty_name,
        "ops_weight_kg": row.ops_weight_kg,
        "ops_unload_address_id": row.ops_unload_address_id,
        "ops_unload_address": unload_address,
        "ops_ship_time": row.ops_ship_time,
        "ops_expense_doc_number": row.ops_expense_doc_number,
        "ops_pallets_ready_time": row.ops_pallets_ready_time,
        "ops_logistics_comment": row.ops_logistics_comment,
        "ops_car_driver": row.ops_car_driver,
        "ops_packer_user_ids": list(row.ops_packer_user_ids or []),
        "ops_packer_display": row.ops_packer_display,
        "created_at_ts": row.created_at_ts,
        "updated_at_ts": row.updated_at_ts,
        "supply_count": row.supply_count,
        "labels_url": row.labels_url,
    }
    if include_details:
        from app.ozon_fbo_ops_sheets import batch_ship_date, ops_editable_from_batch, ops_sheet_for_batch

        data["ops_ship_date"] = batch_ship_date(row)
        data["ops_editable"] = ops_editable_from_batch(
            row,
            default_counterparty_id=default_counterparty_id,
        )
        data["ops_sheet"] = ops_sheet_for_batch(
            row,
            row.supplies,
            counterparty_name=counterparty_name,
            unload_address=unload_address,
            packing_status_name=packing_status_name,
            supply_type_name=supply_type_name,
            cluster_name_map=cluster_name_map,
            default_counterparty_id=default_counterparty_id,
        )
        data["supplies"] = [
            supply_to_dict(s, include_details=True, catalog_map=catalog_map) for s in row.supplies
        ]
    return data


def format_timeslot(from_ts: str, to_ts: str) -> str:
    f = str(from_ts or "").strip()
    t = str(to_ts or "").strip()
    if not f and not t:
        return ""
    if len(f) >= 16 and len(t) >= 16:
        return f"{f[11:16]}–{t[11:16]} ({f[:10]})"
    return f"{f} – {t}".strip(" –")


def delivery_type_label(delivery_type: str) -> str:
    return {
        DELIVERY_DIRECT: "Самостоятельно",
        DELIVERY_CROSSDOCK: "Кросс-док",
    }.get(delivery_type, delivery_type)


def batch_status_label(status: str) -> str:
    return {
        BATCH_STATUS_PLANNING: "Планирование",
        BATCH_STATUS_SUBMITTED: "Создано в Ozon",
        BATCH_STATUS_PACKING: "Сборка грузомест",
        BATCH_STATUS_DONE: "Завершён",
    }.get(status, status)


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
