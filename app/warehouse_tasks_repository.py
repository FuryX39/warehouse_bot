"""Задачи новой панели /warehouse."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, delete, func, nulls_first, nulls_last, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmCounterparty, CrmRepository
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.warehouse_transfers_repository import WarehouseTransfersRepository
from app.warehouse_users_repository import WarehouseUser, WarehouseUsersRepository
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository

_WORK_HOURS_RE = re.compile(r"^\d+([.,]\d+)?$")


class _Base(DeclarativeBase):
    pass


ENTITY_TRANSFER = "transfer"
ENTITY_RECEIPT = "receipt"
ENTITY_WRITEOFF = "writeoff"

ENTITY_LABELS = {
    ENTITY_TRANSFER: "Перемещение",
    ENTITY_RECEIPT: "Оприходование",
    ENTITY_WRITEOFF: "Списание",
}

TASK_LIST_SORT_FIELDS = frozenset(
    {
        "id",
        "status",
        "task_type",
        "author",
        "counterparty",
        "assignees",
        "documents",
        "comment",
        "start_date",
        "end_date",
    }
)

_DEFAULT_TASK_STATUSES: tuple[tuple[str, str], ...] = (
    ("Новый", "#f9a825"),
    ("В работе", "#42a5f5"),
    ("На проверке", "#ab47bc"),
    ("Выполнен", "#66bb6a"),
)


class WarehouseTaskStatus(_Base):
    __tablename__ = "warehouse_task_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#9e9e9e")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WarehouseTaskType(_Base):
    __tablename__ = "warehouse_task_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    coefficient: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseTask(_Base):
    __tablename__ = "warehouse_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_task_types.id"), nullable=False
    )
    comment: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    work_hours: Mapped[str] = mapped_column(String(32), nullable=False, default="0")
    start_date_ts: Mapped[int] = mapped_column(Integer, nullable=True)
    end_date_ts: Mapped[int] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=True)
    counterparty_id: Mapped[int] = mapped_column(Integer, nullable=True)
    status_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_task_statuses.id"), nullable=True
    )
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseTaskAssignee(_Base):
    __tablename__ = "warehouse_task_assignees"

    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class WarehouseTaskDocument(_Base):
    __tablename__ = "warehouse_task_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_tasks.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)


class WarehouseTaskCustomField(_Base):
    __tablename__ = "warehouse_task_custom_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseTaskCustomFieldValue(_Base):
    __tablename__ = "warehouse_task_custom_field_values"

    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    field_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("warehouse_task_custom_fields.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[str] = mapped_column(String(2048), nullable=False, default="")


@dataclass
class TaskCustomFieldValueRow:
    field_id: int
    name: str
    comment: str
    value: str


@dataclass
class TaskAssigneeRow:
    user_id: int
    display_name: str


@dataclass
class TaskDocumentRow:
    entity_type: str
    entity_type_label: str
    entity_id: int
    label: str


@dataclass
class TaskRow:
    id: int
    task_type_id: int
    task_type_name: str
    status_id: int | None
    status_name: str
    status_color: str
    comment: str
    work_hours: float
    start_date_ts: int | None
    end_date_ts: int | None
    created_by_user_id: int | None
    created_by_name: str
    counterparty_id: int | None
    counterparty_name: str
    created_at_ts: int
    updated_at_ts: int
    assignees: list[TaskAssigneeRow] = field(default_factory=list)
    documents: list[TaskDocumentRow] = field(default_factory=list)
    custom_fields: list[TaskCustomFieldValueRow] = field(default_factory=list)


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


def _truncate_comment(comment: str, limit: int = 120) -> str:
    c = str(comment or "").strip()
    if len(c) <= limit:
        return c
    return c[: max(0, limit - 3)].rstrip() + "..."


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _day_to_ts(d: date | None) -> int | None:
    if d is None:
        return None
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _day_end_ts(d: date | None) -> int | None:
    if d is None:
        return None
    return int(
        datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    )


def _ts_to_day_str(ts: int | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


class WarehouseTasksRepository:
    def __init__(
        self,
        db_url: str,
        users_repo: WarehouseUsersRepository,
        receipts_repo: WarehouseReceiptsRepository,
        writeoffs_repo: WarehouseWriteoffsRepository,
        transfers_repo: WarehouseTransfersRepository,
        catalog_repo: CatalogRepository,
        crm_repo: CrmRepository,
    ) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)
        self.users_repo = users_repo
        self.receipts_repo = receipts_repo
        self.writeoffs_repo = writeoffs_repo
        self.transfers_repo = transfers_repo
        self.catalog_repo = catalog_repo
        self.crm_repo = crm_repo

    def init_schema(self) -> None:
        self._ensure_schema()

    def _migrate_counterparty_id_column(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_tasks" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_tasks")}
        if "counterparty_id" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text("ALTER TABLE warehouse_tasks ADD COLUMN counterparty_id INTEGER")
            )
            session.commit()

    def _ensure_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._migrate_counterparty_id_column()
        self._migrate_status_column()
        self._migrate_task_type_coefficient_column()
        self._migrate_task_work_hours_column()
        self._seed_task_statuses()

    def _migrate_task_type_coefficient_column(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_task_types" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_task_types")}
        if "coefficient" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE warehouse_task_types "
                    "ADD COLUMN coefficient VARCHAR(32) NOT NULL DEFAULT '1'"
                )
            )
            session.commit()

    def _migrate_status_column(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_tasks" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_tasks")}
        if "status_id" not in cols:
            with Session(self.engine) as session:
                session.execute(text("ALTER TABLE warehouse_tasks ADD COLUMN status_id INTEGER"))
                session.commit()
        self._seed_task_statuses()
        with Session(self.engine) as session:
            default_id = self._default_status_id(session)
            if default_id is not None:
                session.execute(
                    text(
                        "UPDATE warehouse_tasks SET status_id = :sid "
                        "WHERE status_id IS NULL"
                    ),
                    {"sid": int(default_id)},
                )
                session.commit()

    def _migrate_task_work_hours_column(self) -> None:
        from sqlalchemy import inspect, text

        if "warehouse_tasks" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("warehouse_tasks")}
        if "work_hours" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE warehouse_tasks "
                    "ADD COLUMN work_hours VARCHAR(32) NOT NULL DEFAULT '0'"
                )
            )
            session.commit()

    def _seed_task_statuses(self) -> None:
        with Session(self.engine) as session:
            count = int(
                session.scalar(select(func.count()).select_from(WarehouseTaskStatus)) or 0
            )
            if count > 0:
                return
            for i, (name, color) in enumerate(_DEFAULT_TASK_STATUSES):
                session.add(
                    WarehouseTaskStatus(
                        name=name,
                        color=color,
                        sort_order=i,
                        is_default=(i == 0),
                    )
                )
            session.commit()

    def _default_status_id(self, session: Session) -> int | None:
        row = session.scalar(
            select(WarehouseTaskStatus)
            .where(WarehouseTaskStatus.is_default.is_(True))
            .order_by(WarehouseTaskStatus.sort_order, WarehouseTaskStatus.id)
        )
        if row is not None:
            return int(row.id)
        row = session.scalar(
            select(WarehouseTaskStatus).order_by(
                WarehouseTaskStatus.sort_order, WarehouseTaskStatus.id
            )
        )
        return int(row.id) if row is not None else None

    def get_meta(self) -> dict[str, Any]:
        return {
            "task_types": self.list_task_types(),
            "task_statuses": self.list_task_statuses(),
            "custom_fields": self.list_custom_fields(),
        }

    def list_task_statuses(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseTaskStatus).order_by(
                    WarehouseTaskStatus.sort_order, WarehouseTaskStatus.name
                )
            ).all()
            return [self._task_status_dict(r) for r in rows]

    def save_task_statuses(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {r.id: r for r in session.scalars(select(WarehouseTaskStatus)).all()}
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                color = str(item.get("color") or "#9e9e9e").strip() or "#9e9e9e"
                raw_id = item.get("id")
                row = None
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = WarehouseTaskStatus(name=name[:128], color=color[:16], sort_order=i)
                    session.add(row)
                else:
                    row.name = name[:128]
                    row.color = color[:16]
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for sid, row in existing.items():
                if sid not in keep_ids and not row.is_default:
                    session.delete(row)
            session.commit()
        return self.list_task_statuses()

    def _task_status_dict(self, row: WarehouseTaskStatus) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "name": str(row.name),
            "color": str(row.color or "#9e9e9e"),
            "sort_order": int(row.sort_order),
            "is_default": bool(row.is_default),
        }

    def list_task_types(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseTaskType).order_by(
                    WarehouseTaskType.sort_order, WarehouseTaskType.name
                )
            ).all()
            return [
                {
                    "id": int(r.id),
                    "name": r.name,
                    "comment": r.comment or "",
                    "sort_order": int(r.sort_order),
                }
                for r in rows
            ]

    def save_task_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {r.id: r for r in session.scalars(select(WarehouseTaskType)).all()}
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                row = None
                raw_id = item.get("id")
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = WarehouseTaskType(
                        name=name[:128],
                        comment=str(item.get("comment") or "").strip()[:512],
                        sort_order=i,
                    )
                    session.add(row)
                else:
                    row.name = name[:128]
                    row.comment = str(item.get("comment") or "").strip()[:512]
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids:
                    session.delete(row)
            session.commit()
        return self.list_task_types()

    def get_task_type(self, type_id: int) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskType, int(type_id))
            if row is None:
                return None
            return self._task_type_dict(row)

    def create_task_type(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Название типа задачи обязательно")
        comment = str(data.get("comment") or "").strip()[:512]
        with Session(self.engine) as session:
            if session.scalar(
                select(func.count())
                .select_from(WarehouseTaskType)
                .where(WarehouseTaskType.name == name)
            ):
                raise ValueError("Тип задачи с таким названием уже существует")
            max_order = session.scalar(select(func.max(WarehouseTaskType.sort_order))) or 0
            row = WarehouseTaskType(
                name=name[:128],
                comment=comment,
                sort_order=int(max_order) + 1,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._task_type_dict(row)

    def update_task_type(self, type_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskType, int(type_id))
            if row is None:
                return None
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Название типа задачи обязательно")
                exists = session.scalar(
                    select(func.count())
                    .select_from(WarehouseTaskType)
                    .where(WarehouseTaskType.name == name, WarehouseTaskType.id != int(type_id))
                )
                if exists:
                    raise ValueError("Тип задачи с таким названием уже существует")
                row.name = name[:128]
            if "comment" in data:
                row.comment = str(data.get("comment") or "").strip()[:512]
            if "sort_order" in data:
                try:
                    row.sort_order = int(data.get("sort_order"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Некорректный sort_order") from exc
            session.commit()
            session.refresh(row)
            return self._task_type_dict(row)

    def delete_task_type(self, type_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskType, int(type_id))
            if row is None:
                return False
            in_use = session.scalar(
                select(func.count())
                .select_from(WarehouseTask)
                .where(WarehouseTask.task_type_id == int(type_id))
            )
            if in_use:
                raise ValueError("Нельзя удалить тип: есть связанные задачи")
            session.delete(row)
            session.commit()
        return True

    def _task_type_dict(self, row: WarehouseTaskType) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "name": str(row.name),
            "comment": str(row.comment or ""),
            "sort_order": int(row.sort_order),
        }

    def list_custom_fields(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseTaskCustomField).order_by(
                    WarehouseTaskCustomField.sort_order, WarehouseTaskCustomField.name
                )
            ).all()
            return [self._custom_field_dict(r) for r in rows]

    def save_custom_fields(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_schema()
        with Session(self.engine) as session:
            existing = {
                r.id: r for r in session.scalars(select(WarehouseTaskCustomField)).all()
            }
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                row = None
                raw_id = item.get("id")
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = WarehouseTaskCustomField(
                        name=name[:128],
                        comment=str(item.get("comment") or "").strip()[:512],
                        sort_order=i,
                    )
                    session.add(row)
                else:
                    row.name = name[:128]
                    row.comment = str(item.get("comment") or "").strip()[:512]
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids:
                    session.delete(row)
            session.commit()
        return self.list_custom_fields()

    def get_custom_field(self, field_id: int) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskCustomField, int(field_id))
            if row is None:
                return None
            return self._custom_field_dict(row)

    def create_custom_field(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Название дополнительного поля обязательно")
        comment = str(data.get("comment") or "").strip()[:512]
        with Session(self.engine) as session:
            if session.scalar(
                select(func.count())
                .select_from(WarehouseTaskCustomField)
                .where(WarehouseTaskCustomField.name == name)
            ):
                raise ValueError("Поле с таким названием уже существует")
            max_order = session.scalar(select(func.max(WarehouseTaskCustomField.sort_order))) or 0
            row = WarehouseTaskCustomField(
                name=name[:128],
                comment=comment,
                sort_order=int(max_order) + 1,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._custom_field_dict(row)

    def update_custom_field(self, field_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskCustomField, int(field_id))
            if row is None:
                return None
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Название дополнительного поля обязательно")
                exists = session.scalar(
                    select(func.count())
                    .select_from(WarehouseTaskCustomField)
                    .where(
                        WarehouseTaskCustomField.name == name,
                        WarehouseTaskCustomField.id != int(field_id),
                    )
                )
                if exists:
                    raise ValueError("Поле с таким названием уже существует")
                row.name = name[:128]
            if "comment" in data:
                row.comment = str(data.get("comment") or "").strip()[:512]
            if "sort_order" in data:
                try:
                    row.sort_order = int(data.get("sort_order"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Некорректный sort_order") from exc
            session.commit()
            session.refresh(row)
            return self._custom_field_dict(row)

    def delete_custom_field(self, field_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskCustomField, int(field_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
        return True

    def _custom_field_dict(self, row: WarehouseTaskCustomField) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "name": str(row.name),
            "comment": str(row.comment or ""),
            "sort_order": int(row.sort_order),
        }

    def list_tasks(
        self,
        filters: dict[str, str],
        *,
        limit: int = 500,
        offset: int = 0,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        sort_by2: str | None = None,
        sort_dir2: str | None = None,
    ) -> list[TaskRow]:
        limit = max(1, min(2000, int(limit)))
        offset = max(0, int(offset))
        sort_key, sort_asc = self.parse_list_sort(
            sort_by, sort_dir, default_key="start_date", default_dir="asc"
        )
        sort_key2, sort_asc2 = self.parse_list_sort(
            sort_by2, sort_dir2, default_key="status", default_dir="asc"
        )
        with Session(self.engine) as session:
            q = select(WarehouseTask)
            conds = self._list_filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            q = self._apply_list_sort(q, sort_key, sort_asc, sort_key2, sort_asc2)
            q = q.limit(limit).offset(offset)
            rows = session.scalars(q).all()
            return [self._task_row(session, r) for r in rows]

    def parse_list_sort(
        self,
        sort_by: str | None,
        sort_dir: str | None,
        *,
        default_key: str = "start_date",
        default_dir: str = "asc",
    ) -> tuple[str, bool]:
        key = str(sort_by or default_key).strip().lower()
        if key not in TASK_LIST_SORT_FIELDS:
            key = default_key
        default_asc = str(default_dir or "asc").strip().lower() == "asc"
        if sort_dir is None or str(sort_dir).strip() == "":
            asc = default_asc
        else:
            asc = str(sort_dir).strip().lower() == "asc"
        return key, asc

    def _apply_list_sort(
        self,
        q,
        sort_key: str,
        sort_asc: bool,
        sort_key2: str,
        sort_asc2: bool,
    ):
        joins: set[str] = set()
        order_parts: list = []
        seen_keys: set[str] = set()
        for key, asc in ((sort_key, sort_asc), (sort_key2, sort_asc2)):
            if key in seen_keys:
                continue
            seen_keys.add(key)
            q, joins, exprs = self._sort_key_order(q, key, asc, joins)
            order_parts.extend(exprs)
        from sqlalchemy import asc as sa_asc
        from sqlalchemy import desc as sa_desc

        order_parts.append(sa_desc(WarehouseTask.id))
        return q.order_by(*order_parts)

    def _sort_key_order(self, q, sort_key: str, asc: bool, joins: set[str]):
        from sqlalchemy import asc as sa_asc
        from sqlalchemy import desc as sa_desc

        direction = sa_asc if asc else sa_desc
        exprs: list = []

        if sort_key == "id":
            exprs.append(direction(WarehouseTask.id))
            return q, joins, exprs

        if sort_key == "status":
            if "status" not in joins:
                q = q.outerjoin(
                    WarehouseTaskStatus, WarehouseTask.status_id == WarehouseTaskStatus.id
                )
                joins.add("status")
            col = direction(WarehouseTaskStatus.sort_order)
            exprs.append(nulls_last(col) if asc else nulls_first(col))
            return q, joins, exprs

        if sort_key == "task_type":
            if "task_type" not in joins:
                q = q.outerjoin(
                    WarehouseTaskType, WarehouseTask.task_type_id == WarehouseTaskType.id
                )
                joins.add("task_type")
            exprs.append(direction(WarehouseTaskType.name))
            return q, joins, exprs

        if sort_key == "author":
            if "author" not in joins:
                q = q.outerjoin(
                    WarehouseUser, WarehouseTask.created_by_user_id == WarehouseUser.id
                )
                joins.add("author")
            exprs.append(
                direction(func.coalesce(WarehouseUser.display_name, WarehouseUser.login, ""))
            )
            return q, joins, exprs

        if sort_key == "counterparty":
            if "counterparty" not in joins:
                q = q.outerjoin(
                    CrmCounterparty, WarehouseTask.counterparty_id == CrmCounterparty.id
                )
                joins.add("counterparty")
            exprs.append(direction(func.coalesce(CrmCounterparty.full_name, "")))
            return q, joins, exprs

        if sort_key == "assignees":
            assignee_name = (
                select(func.min(func.coalesce(WarehouseUser.display_name, WarehouseUser.login, "")))
                .select_from(WarehouseTaskAssignee)
                .join(WarehouseUser, WarehouseUser.id == WarehouseTaskAssignee.user_id)
                .where(WarehouseTaskAssignee.task_id == WarehouseTask.id)
                .correlate(WarehouseTask)
                .scalar_subquery()
            )
            col = direction(assignee_name)
            exprs.append(nulls_last(col) if asc else nulls_first(col))
            return q, joins, exprs

        if sort_key == "documents":
            doc_count = (
                select(func.count())
                .select_from(WarehouseTaskDocument)
                .where(WarehouseTaskDocument.task_id == WarehouseTask.id)
                .correlate(WarehouseTask)
                .scalar_subquery()
            )
            exprs.append(direction(doc_count))
            return q, joins, exprs

        if sort_key == "comment":
            exprs.append(direction(WarehouseTask.comment))
            return q, joins, exprs

        if sort_key == "start_date":
            col = direction(WarehouseTask.start_date_ts)
            exprs.append((nulls_first if asc else nulls_last)(col))
            return q, joins, exprs

        col = direction(WarehouseTask.end_date_ts)
        exprs.append((nulls_first if asc else nulls_last)(col))
        return q, joins, exprs

    def count_tasks(self, filters: dict[str, str]) -> int:
        with Session(self.engine) as session:
            q = select(func.count()).select_from(WarehouseTask)
            conds = self._list_filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            return int(session.scalar(q) or 0)

    def get_task(self, task_id: int) -> TaskRow | None:
        with Session(self.engine) as session:
            row = session.get(WarehouseTask, int(task_id))
            if row is None:
                return None
            return self._task_row(session, row)

    def create_task(self, data: dict[str, Any], *, created_by_user_id: int | None) -> TaskRow:
        return self._save_task(None, data, created_by_user_id=created_by_user_id)

    def update_task(self, task_id: int, data: dict[str, Any]) -> TaskRow | None:
        with Session(self.engine) as session:
            if session.get(WarehouseTask, int(task_id)) is None:
                return None
        return self._save_task(int(task_id), data, created_by_user_id=None)

    def patch_task(self, task_id: int, data: dict[str, Any]) -> TaskRow | None:
        current = self.get_task(int(task_id))
        if current is None:
            return None
        merged: dict[str, Any] = {
            "task_type_id": current.task_type_id,
            "status_id": current.status_id,
            "comment": current.comment,
            "work_hours": current.work_hours,
            "start_date": _ts_to_day_str(current.start_date_ts) or None,
            "end_date": _ts_to_day_str(current.end_date_ts) or None,
            "counterparty_id": current.counterparty_id,
            "assignee_ids": [a.user_id for a in current.assignees],
            "documents": [
                {"entity_type": d.entity_type, "entity_id": d.entity_id} for d in current.documents
            ],
            "custom_fields": [
                {"field_id": f.field_id, "value": f.value} for f in current.custom_fields
            ],
        }
        for key in (
            "task_type_id",
            "status_id",
            "comment",
            "work_hours",
            "start_date",
            "end_date",
            "counterparty_id",
            "assignee_ids",
            "documents",
            "custom_fields",
        ):
            if key in data:
                merged[key] = data[key]
        return self._save_task(int(task_id), merged, created_by_user_id=None)

    def bulk_create_tasks(
        self, items: list[dict[str, Any]], *, created_by_user_id: int | None
    ) -> list[TaskRow]:
        if not isinstance(items, list) or not items:
            raise ValueError("items должен быть непустым массивом")
        out: list[TaskRow] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Каждый элемент items должен быть объектом")
            out.append(self.create_task(item, created_by_user_id=created_by_user_id))
        return out

    def list_tasks_by_document(self, entity_type: str, entity_id: int) -> list[TaskRow]:
        entity_type = str(entity_type or "").strip().lower()
        if entity_type not in ENTITY_LABELS:
            raise ValueError("Некорректный тип документа")
        return self.list_tasks(
            {"entity_type": entity_type, "entity_id": str(int(entity_id))},
            limit=500,
            offset=0,
        )

    def set_assignees(self, task_id: int, assignee_ids: list[int]) -> TaskRow | None:
        return self.patch_task(task_id, {"assignee_ids": assignee_ids})

    def add_assignees(self, task_id: int, assignee_ids: list[int]) -> TaskRow | None:
        current = self.get_task(int(task_id))
        if current is None:
            return None
        merged = {a.user_id for a in current.assignees}
        for uid in self._normalize_assignee_ids(assignee_ids):
            merged.add(uid)
        return self.patch_task(task_id, {"assignee_ids": sorted(merged)})

    def remove_assignee(self, task_id: int, user_id: int) -> TaskRow | None:
        current = self.get_task(int(task_id))
        if current is None:
            return None
        merged = [a.user_id for a in current.assignees if a.user_id != int(user_id)]
        return self.patch_task(task_id, {"assignee_ids": merged})

    def set_documents(self, task_id: int, documents: list[dict[str, Any]]) -> TaskRow | None:
        return self.patch_task(task_id, {"documents": documents})

    def link_document(
        self, task_id: int, entity_type: str, entity_id: int
    ) -> TaskRow | None:
        current = self.get_task(int(task_id))
        if current is None:
            return None
        docs = [{"entity_type": d.entity_type, "entity_id": d.entity_id} for d in current.documents]
        docs.append({"entity_type": entity_type, "entity_id": entity_id})
        return self.patch_task(task_id, {"documents": docs})

    def unlink_document(
        self, task_id: int, entity_type: str, entity_id: int
    ) -> TaskRow | None:
        current = self.get_task(int(task_id))
        if current is None:
            return None
        entity_type = str(entity_type or "").strip().lower()
        docs = [
            {"entity_type": d.entity_type, "entity_id": d.entity_id}
            for d in current.documents
            if not (d.entity_type == entity_type and d.entity_id == int(entity_id))
        ]
        return self.patch_task(task_id, {"documents": docs})

    def hours_summary_calendar(
        self, *, year: int, month: int, filters: dict[str, str] | None = None
    ) -> dict[str, Any]:
        from calendar import monthrange

        year = int(year)
        month = int(month)
        if month < 1 or month > 12:
            raise ValueError("Некорректный месяц")

        date_from = date(year, month, 1)
        date_to = date(year, month, monthrange(year, month)[1])
        merged = dict(filters or {})
        for key in ("start_date_from", "start_date_to", "end_date_from", "end_date_to"):
            merged.pop(key, None)

        with Session(self.engine) as session:
            q = select(WarehouseTask).order_by(
                WarehouseTask.start_date_ts.asc(), WarehouseTask.id.asc()
            )
            conds = [
                WarehouseTask.start_date_ts.is_not(None),
                WarehouseTask.start_date_ts >= _day_to_ts(date_from),
                WarehouseTask.start_date_ts <= _day_end_ts(date_to),
            ]
            extra = self._list_filter_conditions(session, merged)
            if extra:
                conds.extend(extra)
            q = q.where(*conds).limit(5000)
            task_rows = session.scalars(q).all()
            rows = [self._task_row(session, r) for r in task_rows]

        days_out: dict[str, dict[str, Any]] = {}
        for row in rows:
            day_key = _ts_to_day_str(row.start_date_ts)
            if not day_key:
                continue
            bucket = days_out.setdefault(
                day_key,
                {"total_task_hours": 0.0, "task_count": 0},
            )
            bucket["task_count"] += 1
            bucket["total_task_hours"] = float(bucket["total_task_hours"]) + float(
                row.work_hours or 0
            )

        return {
            "year": year,
            "month": month,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days": days_out,
            "total_tasks": len(rows),
        }

    def planning_calendar(
        self, *, date_from: str, date_to: str, filters: dict[str, str] | None = None
    ) -> dict[str, Any]:
        merged = dict(filters or {})
        if date_from:
            merged["end_date_from"] = date_from
        if date_to:
            merged["end_date_to"] = date_to
        rows = self.list_tasks(merged, limit=2000, offset=0)
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            day = _ts_to_day_str(row.end_date_ts) or "_no_end_date"
            by_day.setdefault(day, []).append(self.task_to_dict(row))
        days = []
        for day in sorted(by_day.keys(), key=lambda d: (d == "_no_end_date", d), reverse=True):
            tasks = by_day[day]
            days.append(
                {
                    "date": None if day == "_no_end_date" else day,
                    "task_count": len(tasks),
                    "tasks": tasks,
                }
            )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "total_tasks": len(rows),
            "days": days,
        }

    def planning_summary(
        self,
        *,
        date_from: str,
        date_to: str,
        group_by: str = "day",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        merged = dict(filters or {})
        if date_from:
            merged["end_date_from"] = date_from
        if date_to:
            merged["end_date_to"] = date_to
        rows = self.list_tasks(merged, limit=2000, offset=0)
        group_by = str(group_by or "day").strip().lower()
        groups_map: dict[str, dict[str, Any]] = {}

        def bump(key: str, label: str, task_id: int, extra: dict[str, Any] | None = None) -> None:
            bucket = groups_map.setdefault(
                key,
                {"key": key, "label": label, "task_count": 0, "task_ids": [], **(extra or {})},
            )
            bucket["task_count"] += 1
            bucket["task_ids"].append(task_id)

        for row in rows:
            task_id = int(row.id)
            if group_by == "task_type":
                bump(
                    f"type:{row.task_type_id}",
                    row.task_type_name or f"Тип #{row.task_type_id}",
                    task_id,
                    {"task_type_id": row.task_type_id},
                )
            elif group_by == "assignee":
                if not row.assignees:
                    bump("assignee:none", "Без ответственного", task_id)
                for a in row.assignees:
                    bump(
                        f"assignee:{a.user_id}",
                        a.display_name,
                        task_id,
                        {"user_id": a.user_id},
                    )
            else:
                day = _ts_to_day_str(row.end_date_ts) or "_no_end_date"
                label = "Без даты окончания" if day == "_no_end_date" else day
                bump(f"day:{day}", label, task_id, {"date": None if day == "_no_end_date" else day})

        groups = sorted(
            groups_map.values(),
            key=lambda g: (str(g.get("key", "")).startswith("day:_no"), str(g.get("label", ""))),
            reverse=True,
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "group_by": group_by,
            "total_tasks": len(rows),
            "groups": groups,
        }

    def api_schema(self) -> dict[str, Any]:
        return {
            "version": "1",
            "auth": {
                "session": "Cookie сессии панели /warehouse (как в браузере)",
                "bearer_token": "Authorization: Bearer <WAREHOUSE_TASKS_API_TOKEN> или заголовок X-Api-Key",
            },
            "document_entity_types": [
                {"id": key, "title": label} for key, label in ENTITY_LABELS.items()
            ],
            "task_fields": {
                "task_type_id": "int, обязателен при создании",
                "comment": "string",
                "work_hours": "decimal >= 0, часы на выполнение задачи",
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "counterparty_id": "int, id контрагента из CRM",
                "assignee_ids": "[int]",
                "documents": '[{"entity_type":"transfer|receipt|writeoff","entity_id":int}]',
                "custom_fields": '[{"field_id":int,"value":"string"}]',
                "created_by_user_id": "int, только при вызове по API-токену без сессии",
            },
            "list_filters": [
                "q",
                "comment",
                "task_type_id",
                "assignee_id",
                "created_by_user_id",
                "counterparty_id",
                "entity_type",
                "entity_id",
                "start_date_from",
                "start_date_to",
                "end_date_from",
                "end_date_to",
                "limit",
                "offset",
            ],
        }

    def delete_task(self, task_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(WarehouseTask, int(task_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
        return True

    def search_documents(
        self, *, q: str = "", entity_type: str = "", limit: int = 40
    ) -> list[dict[str, Any]]:
        q = str(q or "").strip()
        entity_type = str(entity_type or "").strip().lower()
        filters = {"q": q, "title": q} if q else {}
        out: list[dict[str, Any]] = []

        if not entity_type or entity_type == ENTITY_TRANSFER:
            for row in self.transfers_repo.list_transfers(filters):
                out.append(self._document_search_item(ENTITY_TRANSFER, row.id, row.display_name, row.created_at_ts))
        if not entity_type or entity_type == ENTITY_RECEIPT:
            for row in self.receipts_repo.list_receipts(filters):
                out.append(self._document_search_item(ENTITY_RECEIPT, row.id, row.display_name, row.created_at_ts))
        if not entity_type or entity_type == ENTITY_WRITEOFF:
            for row in self.writeoffs_repo.list_writeoffs(filters):
                out.append(self._document_search_item(ENTITY_WRITEOFF, row.id, row.display_name, row.created_at_ts))

        out.sort(key=lambda x: (-int(x.get("created_at_ts") or 0), str(x.get("label") or "")))
        return out[: max(1, min(100, int(limit)))]

    def task_to_dict(self, row: TaskRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_type_id": row.task_type_id,
            "task_type_name": row.task_type_name,
            "status_id": row.status_id,
            "status_name": row.status_name,
            "status_color": row.status_color,
            "comment": row.comment,
            "comment_short": _truncate_comment(row.comment),
            "work_hours": row.work_hours,
            "start_date": _ts_to_day_str(row.start_date_ts),
            "end_date": _ts_to_day_str(row.end_date_ts),
            "start_date_ts": row.start_date_ts,
            "end_date_ts": row.end_date_ts,
            "created_by_user_id": row.created_by_user_id,
            "created_by_name": row.created_by_name,
            "author_name": row.created_by_name,
            "counterparty_id": row.counterparty_id,
            "counterparty_name": row.counterparty_name,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
            "assignee_ids": [a.user_id for a in row.assignees],
            "assignees": [
                {"user_id": a.user_id, "display_name": a.display_name} for a in row.assignees
            ],
            "assignees_short": ", ".join(a.display_name for a in row.assignees) or "—",
            "documents": [
                {
                    "entity_type": d.entity_type,
                    "entity_type_label": d.entity_type_label,
                    "entity_id": d.entity_id,
                    "label": d.label,
                }
                for d in row.documents
            ],
            "documents_short": ", ".join(d.label for d in row.documents) or "—",
            "custom_fields": [
                {
                    "field_id": f.field_id,
                    "name": f.name,
                    "comment": f.comment,
                    "value": f.value,
                }
                for f in row.custom_fields
            ],
            "custom_fields_short": ", ".join(
                f"{f.name}: {f.value}" for f in row.custom_fields if str(f.value or "").strip()
            )
            or "—",
        }

    def _document_search_item(
        self, entity_type: str, entity_id: int, label: str, created_at_ts: int
    ) -> dict[str, Any]:
        return {
            "entity_type": entity_type,
            "entity_type_label": ENTITY_LABELS.get(entity_type, entity_type),
            "entity_id": int(entity_id),
            "label": str(label or ""),
            "created_at_ts": int(created_at_ts or 0),
        }

    def _save_task(
        self,
        task_id: int | None,
        data: dict[str, Any],
        *,
        created_by_user_id: int | None,
    ) -> TaskRow:
        try:
            task_type_id = int(data.get("task_type_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Выберите тип задачи") from exc
        comment = str(data.get("comment") or "").strip()[:2048]
        work_hours = _work_hours_storage(data.get("work_hours"))
        start_date_ts = _day_to_ts(_parse_day(data.get("start_date")))
        end_date_ts = _day_to_ts(_parse_day(data.get("end_date")))
        if start_date_ts and end_date_ts and end_date_ts < start_date_ts:
            raise ValueError("Дата окончания не может быть раньше даты начала")

        assignee_ids = self._normalize_assignee_ids(data.get("assignee_ids"))
        documents = self._normalize_documents(data.get("documents"))
        custom_fields = self._normalize_custom_fields(data.get("custom_fields"))
        counterparty_id = self._normalize_counterparty_id(data.get("counterparty_id"))
        now = int(time.time())
        self._ensure_schema()

        with Session(self.engine) as session:
            status_id = self._normalize_status_id(session, data.get("status_id"))
            if session.get(WarehouseTaskType, task_type_id) is None:
                raise ValueError("Тип задачи не найден")
            for uid in assignee_ids:
                if self.users_repo.get_by_id(uid) is None:
                    raise ValueError(f"Сотрудник id={uid} не найден")
            self._validate_documents(documents)
            for field_id, value in custom_fields.items():
                if not value:
                    continue
                if session.get(WarehouseTaskCustomField, field_id) is None:
                    raise ValueError(f"Дополнительное поле id={field_id} не найдено")

            if task_id is None:
                task = WarehouseTask(
                    task_type_id=task_type_id,
                    comment=comment,
                    work_hours=work_hours,
                    start_date_ts=start_date_ts,
                    end_date_ts=end_date_ts,
                    created_by_user_id=int(created_by_user_id) if created_by_user_id else None,
                    counterparty_id=counterparty_id,
                    status_id=status_id,
                    created_at_ts=now,
                )
                session.add(task)
            else:
                task = session.get(WarehouseTask, int(task_id))
                if task is None:
                    raise ValueError("Задача не найдена")
                task.task_type_id = task_type_id
                task.comment = comment
                task.work_hours = work_hours
                task.start_date_ts = start_date_ts
                task.end_date_ts = end_date_ts
                task.counterparty_id = counterparty_id
                task.status_id = status_id
                session.execute(
                    delete(WarehouseTaskAssignee).where(
                        WarehouseTaskAssignee.task_id == int(task_id)
                    )
                )
                session.execute(
                    delete(WarehouseTaskDocument).where(
                        WarehouseTaskDocument.task_id == int(task_id)
                    )
                )
                session.execute(
                    delete(WarehouseTaskCustomFieldValue).where(
                        WarehouseTaskCustomFieldValue.task_id == int(task_id)
                    )
                )

            session.flush()
            for uid in assignee_ids:
                session.add(WarehouseTaskAssignee(task_id=int(task.id), user_id=int(uid)))
            for doc in documents:
                session.add(
                    WarehouseTaskDocument(
                        task_id=int(task.id),
                        entity_type=doc["entity_type"],
                        entity_id=int(doc["entity_id"]),
                    )
                )
            for field_id, value in custom_fields.items():
                if not value:
                    continue
                session.add(
                    WarehouseTaskCustomFieldValue(
                        task_id=int(task.id),
                        field_id=int(field_id),
                        value=value,
                    )
                )
            task.updated_at_ts = now
            session.commit()
            session.refresh(task)
            return self._task_row(session, task)

    def _normalize_assignee_ids(self, raw: Any) -> list[int]:
        if not isinstance(raw, list):
            return []
        out: list[int] = []
        seen: set[int] = set()
        for item in raw:
            try:
                uid = int(item)
            except (TypeError, ValueError):
                continue
            if uid in seen:
                continue
            seen.add(uid)
            out.append(uid)
        return out

    def _normalize_documents(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        seen: set[tuple[str, int]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            entity_type = str(item.get("entity_type") or "").strip().lower()
            if entity_type not in ENTITY_LABELS:
                continue
            try:
                entity_id = int(item.get("entity_id"))
            except (TypeError, ValueError):
                continue
            key = (entity_type, entity_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"entity_type": entity_type, "entity_id": entity_id})
        return out

    def _normalize_custom_fields(self, raw: Any) -> dict[int, str]:
        if not isinstance(raw, list):
            return {}
        out: dict[int, str] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                field_id = int(item.get("field_id"))
            except (TypeError, ValueError):
                continue
            out[field_id] = str(item.get("value") or "").strip()[:2048]
        return out

    def _normalize_counterparty_id(self, raw: Any) -> int | None:
        if raw is None or raw == "":
            return None
        try:
            counterparty_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный контрагент") from exc
        if counterparty_id <= 0:
            return None
        if self.crm_repo.get_counterparty(counterparty_id) is None:
            raise ValueError(f"Контрагент id={counterparty_id} не найден")
        return counterparty_id

    def _normalize_status_id(self, session: Session, raw: Any) -> int | None:
        if raw is None or raw == "":
            return self._default_status_id(session)
        try:
            status_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный статус") from exc
        if status_id <= 0:
            return self._default_status_id(session)
        if session.get(WarehouseTaskStatus, status_id) is None:
            raise ValueError(f"Статус id={status_id} не найден")
        return status_id

    def _validate_documents(self, documents: list[dict[str, Any]]) -> None:
        for doc in documents:
            entity_type = doc["entity_type"]
            entity_id = int(doc["entity_id"])
            if entity_type == ENTITY_TRANSFER:
                if self.transfers_repo.get_transfer(entity_id) is None:
                    raise ValueError(f"Перемещение id={entity_id} не найдено")
            elif entity_type == ENTITY_RECEIPT:
                if self.receipts_repo.get_receipt(entity_id) is None:
                    raise ValueError(f"Оприходование id={entity_id} не найдено")
            elif entity_type == ENTITY_WRITEOFF:
                if self.writeoffs_repo.get_writeoff(entity_id) is None:
                    raise ValueError(f"Списание id={entity_id} не найдено")

    def _resolve_document_label(self, entity_type: str, entity_id: int) -> str:
        if entity_type == ENTITY_TRANSFER:
            row = self.transfers_repo.get_transfer(entity_id)
            return row.display_name if row else f"Перемещение #{entity_id}"
        if entity_type == ENTITY_RECEIPT:
            row = self.receipts_repo.get_receipt(entity_id)
            return row.display_name if row else f"Оприходование #{entity_id}"
        if entity_type == ENTITY_WRITEOFF:
            row = self.writeoffs_repo.get_writeoff(entity_id)
            return row.display_name if row else f"Списание #{entity_id}"
        return f"{entity_type} #{entity_id}"

    def _task_row(self, session: Session, row: WarehouseTask) -> TaskRow:
        task_type_name = ""
        tt = session.get(WarehouseTaskType, int(row.task_type_id))
        if tt:
            task_type_name = str(tt.name)
        created_by_name = ""
        if row.created_by_user_id:
            creator = self.users_repo.get_by_id(int(row.created_by_user_id))
            if creator:
                created_by_name = str(creator.display_name or creator.login)

        counterparty_name = ""
        if row.counterparty_id:
            cp = self.crm_repo.get_counterparty(int(row.counterparty_id))
            if cp:
                counterparty_name = str(cp.full_name or "").strip() or f"Контрагент #{cp.id}"

        status_name = ""
        status_color = "#9e9e9e"
        if row.status_id:
            st = session.get(WarehouseTaskStatus, int(row.status_id))
            if st:
                status_name = str(st.name)
                status_color = str(st.color or "#9e9e9e")

        assignee_rows = session.scalars(
            select(WarehouseTaskAssignee).where(WarehouseTaskAssignee.task_id == int(row.id))
        ).all()
        assignees: list[TaskAssigneeRow] = []
        for ar in assignee_rows:
            user = self.users_repo.get_by_id(int(ar.user_id))
            if user is None:
                continue
            assignees.append(
                TaskAssigneeRow(
                    user_id=int(user.id),
                    display_name=str(user.display_name or user.login),
                )
            )
        assignees.sort(key=lambda a: a.display_name.lower())

        doc_rows = session.scalars(
            select(WarehouseTaskDocument)
            .where(WarehouseTaskDocument.task_id == int(row.id))
            .order_by(WarehouseTaskDocument.id)
        ).all()
        documents: list[TaskDocumentRow] = []
        for dr in doc_rows:
            entity_type = str(dr.entity_type)
            entity_id = int(dr.entity_id)
            documents.append(
                TaskDocumentRow(
                    entity_type=entity_type,
                    entity_type_label=ENTITY_LABELS.get(entity_type, entity_type),
                    entity_id=entity_id,
                    label=self._resolve_document_label(entity_type, entity_id),
                )
            )

        value_rows = session.scalars(
            select(WarehouseTaskCustomFieldValue).where(
                WarehouseTaskCustomFieldValue.task_id == int(row.id)
            )
        ).all()
        values_by_field = {int(v.field_id): str(v.value or "") for v in value_rows}
        field_defs = session.scalars(
            select(WarehouseTaskCustomField).order_by(
                WarehouseTaskCustomField.sort_order, WarehouseTaskCustomField.name
            )
        ).all()
        custom_fields: list[TaskCustomFieldValueRow] = []
        for field_def in field_defs:
            custom_fields.append(
                TaskCustomFieldValueRow(
                    field_id=int(field_def.id),
                    name=str(field_def.name),
                    comment=str(field_def.comment or ""),
                    value=values_by_field.get(int(field_def.id), ""),
                )
            )

        return TaskRow(
            id=int(row.id),
            task_type_id=int(row.task_type_id),
            task_type_name=task_type_name,
            status_id=int(row.status_id) if row.status_id else None,
            status_name=status_name,
            status_color=status_color,
            comment=str(row.comment or ""),
            work_hours=_work_hours_float(row.work_hours),
            start_date_ts=int(row.start_date_ts) if row.start_date_ts is not None else None,
            end_date_ts=int(row.end_date_ts) if row.end_date_ts is not None else None,
            created_by_user_id=int(row.created_by_user_id) if row.created_by_user_id else None,
            created_by_name=created_by_name,
            counterparty_id=int(row.counterparty_id) if row.counterparty_id else None,
            counterparty_name=counterparty_name,
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
            assignees=assignees,
            documents=documents,
            custom_fields=custom_fields,
        )

    def _list_filter_conditions(self, session: Session, filters: dict[str, str]) -> list:
        conds = []
        raw_type = (filters.get("task_type_id") or "").strip()
        if raw_type:
            try:
                conds.append(WarehouseTask.task_type_id == int(raw_type))
            except ValueError:
                pass
        raw_creator = (filters.get("created_by_user_id") or "").strip()
        if raw_creator:
            try:
                conds.append(WarehouseTask.created_by_user_id == int(raw_creator))
            except ValueError:
                pass
        raw_counterparty = (filters.get("counterparty_id") or "").strip()
        if raw_counterparty:
            try:
                conds.append(WarehouseTask.counterparty_id == int(raw_counterparty))
            except ValueError:
                pass
        raw_status = (filters.get("status_id") or "").strip()
        if raw_status:
            try:
                conds.append(WarehouseTask.status_id == int(raw_status))
            except ValueError:
                pass
        raw_assignee = (filters.get("assignee_id") or "").strip()
        if raw_assignee:
            try:
                assignee_id = int(raw_assignee)
            except ValueError:
                assignee_id = None
            if assignee_id is not None:
                subq = select(WarehouseTaskAssignee.task_id).where(
                    WarehouseTaskAssignee.user_id == assignee_id
                )
                conds.append(WarehouseTask.id.in_(subq))
        raw_entity_type = (filters.get("entity_type") or "").strip().lower()
        raw_entity_id = (filters.get("entity_id") or "").strip()
        if raw_entity_type in ENTITY_LABELS and raw_entity_id:
            try:
                entity_id = int(raw_entity_id)
            except ValueError:
                entity_id = None
            if entity_id is not None:
                subq = select(WarehouseTaskDocument.task_id).where(
                    WarehouseTaskDocument.entity_type == raw_entity_type,
                    WarehouseTaskDocument.entity_id == entity_id,
                )
                conds.append(WarehouseTask.id.in_(subq))
        start_from = _day_to_ts(_parse_day(filters.get("start_date_from")))
        start_to = _day_to_ts(_parse_day(filters.get("start_date_to")))
        if start_from is not None:
            conds.append(WarehouseTask.start_date_ts >= start_from)
        if start_to is not None:
            conds.append(WarehouseTask.start_date_ts <= start_to)
        end_from = _day_to_ts(_parse_day(filters.get("end_date_from")))
        end_to = _day_to_ts(_parse_day(filters.get("end_date_to")))
        if end_from is not None:
            conds.append(WarehouseTask.end_date_ts >= end_from)
        if end_to is not None:
            conds.append(WarehouseTask.end_date_ts <= end_to)
        pat_comment = _like(filters.get("comment", ""))
        if pat_comment:
            conds.append(WarehouseTask.comment.ilike(pat_comment))
        q_text = _like(filters.get("q", ""))
        if q_text:
            type_ids = session.scalars(
                select(WarehouseTaskType.id).where(WarehouseTaskType.name.ilike(q_text))
            ).all()
            or_conds = [WarehouseTask.comment.ilike(q_text)]
            if type_ids:
                or_conds.append(WarehouseTask.task_type_id.in_(type_ids))
            assignee_subq = select(WarehouseTaskAssignee.task_id).where(
                WarehouseTaskAssignee.user_id.in_(
                    select(WarehouseUser.id).where(
                        or_(
                            WarehouseUser.display_name.ilike(q_text),
                            WarehouseUser.login.ilike(q_text),
                        )
                    )
                )
            )
            or_conds.append(WarehouseTask.id.in_(assignee_subq))
            conds.append(or_(*or_conds))
        return conds


def parse_work_hours(value: Any) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    raw = str(value).strip().replace(",", ".")
    if not _WORK_HOURS_RE.match(raw):
        raise ValueError("Часы должны быть неотрицательным числом")
    from decimal import Decimal, InvalidOperation

    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Некорректное значение часов") from exc
    if amount < 0:
        raise ValueError("Часы не могут быть отрицательными")
    return float(amount)


def _work_hours_float(value: Any) -> float:
    try:
        return parse_work_hours(value)
    except ValueError:
        return 0.0


def _work_hours_storage(value: Any) -> str:
    hours = parse_work_hours(value)
    text = f"{hours:.4f}".rstrip("0").rstrip(".")
    return text or "0"
