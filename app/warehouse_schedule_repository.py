"""График работы сотрудников."""

from __future__ import annotations

import calendar
import time
from typing import Any

from sqlalchemy import Integer, String, UniqueConstraint, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_users_repository import WarehouseUser


class _Base(DeclarativeBase):
    pass


class WarehouseEmployeeScheduleEntry(_Base):
    __tablename__ = "warehouse_employee_schedule"
    __table_args__ = (
        UniqueConstraint("user_id", "work_date", name="uq_employee_schedule_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    work_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def _pad2(n: int) -> str:
    return f"{n:02d}"


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    if month < 1 or month > 12:
        raise ValueError("month должен быть от 1 до 12")
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{_pad2(month)}-01", f"{year}-{_pad2(month)}-{_pad2(last_day)}"


def _validate_work_date(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise ValueError("Некорректная дата, ожидается YYYY-MM-DD")
    try:
        year = int(raw[0:4])
        month = int(raw[5:7])
        day = int(raw[8:10])
        if month < 1 or month > 12:
            raise ValueError
        last_day = calendar.monthrange(year, month)[1]
        if day < 1 or day > last_day:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Некорректная дата") from exc
    return raw


class WarehouseScheduleRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def get_month_calendar(
        self, year: int, month: int, user_id: int | None = None
    ) -> dict[str, Any]:
        date_from, date_to = _month_bounds(year, month)
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    WarehouseEmployeeScheduleEntry.work_date,
                    WarehouseEmployeeScheduleEntry.user_id,
                    WarehouseUser.display_name,
                    WarehouseUser.login,
                )
                .join(WarehouseUser, WarehouseUser.id == WarehouseEmployeeScheduleEntry.user_id)
                .where(
                    WarehouseEmployeeScheduleEntry.work_date >= date_from,
                    WarehouseEmployeeScheduleEntry.work_date <= date_to,
                )
                .order_by(
                    WarehouseEmployeeScheduleEntry.work_date,
                    WarehouseUser.display_name,
                    WarehouseUser.login,
                )
            ).all()

        by_date: dict[str, list[dict[str, Any]]] = {}
        for work_date, uid, display_name, login in rows:
            name = str(display_name or login or "").strip() or f"#{uid}"
            by_date.setdefault(str(work_date), []).append({"id": int(uid), "name": name})

        days: dict[str, dict[str, Any]] = {}
        selected_id = int(user_id) if user_id is not None else None
        for work_date, staff in by_date.items():
            staff_ids = {int(item["id"]) for item in staff}
            days[work_date] = {
                "staff_count": len(staff),
                "staff": staff,
                "assigned": selected_id in staff_ids if selected_id is not None else False,
            }

        return {
            "year": int(year),
            "month": int(month),
            "date_from": date_from,
            "date_to": date_to,
            "user_id": selected_id,
            "days": days,
        }

    def staff_counts_for_month(self, year: int, month: int) -> dict[str, int]:
        date_from, date_to = _month_bounds(year, month)
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    WarehouseEmployeeScheduleEntry.work_date,
                    func.count(WarehouseEmployeeScheduleEntry.id),
                )
                .where(
                    WarehouseEmployeeScheduleEntry.work_date >= date_from,
                    WarehouseEmployeeScheduleEntry.work_date <= date_to,
                )
                .group_by(WarehouseEmployeeScheduleEntry.work_date)
            ).all()
        return {str(work_date): int(count) for work_date, count in rows}

    def toggle_day(self, user_id: int, work_date: str) -> bool:
        work_date = _validate_work_date(work_date)
        uid = int(user_id)
        with Session(self.engine) as session:
            if session.get(WarehouseUser, uid) is None:
                raise ValueError("Сотрудник не найден")
            row = session.scalar(
                select(WarehouseEmployeeScheduleEntry).where(
                    WarehouseEmployeeScheduleEntry.user_id == uid,
                    WarehouseEmployeeScheduleEntry.work_date == work_date,
                )
            )
            if row is None:
                session.add(
                    WarehouseEmployeeScheduleEntry(
                        user_id=uid,
                        work_date=work_date,
                        created_at_ts=int(time.time()),
                    )
                )
                session.commit()
                return True
            session.delete(row)
            session.commit()
            return False

    def clear_user_month(self, user_id: int, year: int, month: int) -> int:
        date_from, date_to = _month_bounds(year, month)
        uid = int(user_id)
        with Session(self.engine) as session:
            if session.get(WarehouseUser, uid) is None:
                raise ValueError("Сотрудник не найден")
            result = session.execute(
                delete(WarehouseEmployeeScheduleEntry).where(
                    WarehouseEmployeeScheduleEntry.user_id == uid,
                    WarehouseEmployeeScheduleEntry.work_date >= date_from,
                    WarehouseEmployeeScheduleEntry.work_date <= date_to,
                )
            )
            session.commit()
            return int(result.rowcount or 0)
