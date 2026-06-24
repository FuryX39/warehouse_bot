"""Коэффициенты для сводной по задачам."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.warehouse_schedule_repository import _month_bounds, _validate_work_date

_DEFAULT_KEY = "default"
_COEF_RE = re.compile(r"^\d+([.,]\d+)?$")


class _Base(DeclarativeBase):
    pass


class WarehouseTaskSummaryCoefficient(_Base):
    __tablename__ = "warehouse_task_summary_coefficients"

    work_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    coefficient: Mapped[str] = mapped_column(String(32), nullable=False)


def parse_coefficient(value: Any, *, required: bool = True) -> float | None:
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError("Укажите коэффициент")
        return None
    raw = str(value).strip().replace(",", ".")
    if not _COEF_RE.match(raw):
        raise ValueError("Коэффициент должен быть положительным числом")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Некорректный коэффициент") from exc
    if amount <= 0:
        raise ValueError("Коэффициент должен быть больше нуля")
    return float(amount)


def compute_summary_hours(total_task_hours: float, staff_count: int, coefficient: float) -> float | None:
    if staff_count <= 0 or coefficient <= 0:
        return None
    hours = float(total_task_hours or 0)
    if hours <= 0:
        return 0.0
    return hours / float(staff_count) / float(coefficient)


class WarehouseTaskSummaryRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def get_default_coefficient(self) -> float:
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskSummaryCoefficient, _DEFAULT_KEY)
            if row is None:
                return 1.0
            try:
                return parse_coefficient(row.coefficient) or 1.0
            except ValueError:
                return 1.0

    def set_default_coefficient(self, value: Any) -> float:
        coef = parse_coefficient(value)
        assert coef is not None
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskSummaryCoefficient, _DEFAULT_KEY)
            if row is None:
                session.add(
                    WarehouseTaskSummaryCoefficient(work_date=_DEFAULT_KEY, coefficient=str(coef))
                )
            else:
                row.coefficient = str(coef)
            session.commit()
        return coef

    def get_month_coefficient_map(self, year: int, month: int) -> dict[str, float]:
        date_from, date_to = _month_bounds(year, month)
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WarehouseTaskSummaryCoefficient).where(
                    WarehouseTaskSummaryCoefficient.work_date >= date_from,
                    WarehouseTaskSummaryCoefficient.work_date <= date_to,
                )
            ).all()
        out: dict[str, float] = {}
        for row in rows:
            if row.work_date == _DEFAULT_KEY:
                continue
            try:
                coef = parse_coefficient(row.coefficient)
            except ValueError:
                continue
            if coef is not None:
                out[str(row.work_date)] = coef
        return out

    def set_day_coefficient(self, work_date: str, value: Any | None) -> float | None:
        work_date = _validate_work_date(work_date)
        if value is None or str(value).strip() == "":
            with Session(self.engine) as session:
                row = session.get(WarehouseTaskSummaryCoefficient, work_date)
                if row is not None:
                    session.delete(row)
                    session.commit()
            return None
        coef = parse_coefficient(value)
        with Session(self.engine) as session:
            row = session.get(WarehouseTaskSummaryCoefficient, work_date)
            if row is None:
                session.add(
                    WarehouseTaskSummaryCoefficient(work_date=work_date, coefficient=str(coef))
                )
            else:
                row.coefficient = str(coef)
            session.commit()
        return coef

    def enrich_summary_days(
        self,
        *,
        year: int,
        month: int,
        days: dict[str, dict[str, Any]],
        staff_counts: dict[str, int],
    ) -> dict[str, Any]:
        default_coef = self.get_default_coefficient()
        overrides = self.get_month_coefficient_map(year, month)
        all_keys = set(days.keys()) | set(staff_counts.keys()) | set(overrides.keys())

        enriched: dict[str, dict[str, Any]] = {}
        for day_key in all_keys:
            base = dict(days.get(day_key) or {})
            staff_count = int(staff_counts.get(day_key, 0) or 0)
            override = overrides.get(day_key)
            effective_coef = override if override is not None else default_coef
            total_task_hours = float(base.get("total_task_hours") or 0)
            hours = compute_summary_hours(total_task_hours, staff_count, effective_coef)
            enriched[day_key] = {
                **base,
                "staff_count": staff_count,
                "coefficient": effective_coef,
                "coefficient_override": override,
                "hours": hours,
            }

        return {
            "default_coefficient": default_coef,
            "days": enriched,
        }
