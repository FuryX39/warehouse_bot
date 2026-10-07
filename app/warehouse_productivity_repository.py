"""Краткий отчёт по выработке упаковщиков из завершённых строк заданий."""

from __future__ import annotations

import os
from calendar import monthrange
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, String, Text, UniqueConstraint, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db import create_db_engine


_TYPE_NAMES = {
    "fbs": "FBS",
    "wb_fbo": "FBO WB",
    "wb_fbo_new": "FBO WB new",
    "ym_fbo": "FBO YM",
    "vseinstrumenti": "ВсеИнструменты",
    "rework": "Доработка",
}
_RATE_TYPES = (
    ("fbs", "FBS"),
    ("wb_fbo", "WB FBO"),
    ("ym_fbo", "YM FBO"),
    ("ozon_fbo", "Ozon FBO"),
    ("vseinstrumenti", "ВсеИнструменты"),
    ("rework", "Доработка"),
)
_RATE_IDS = {key for key, _ in _RATE_TYPES}
_RATE_KEY = {
    "fbs": "fbs",
    "wb_fbo": "wb_fbo",
    "wb_fbo_new": "wb_fbo",
    "ym_fbo": "ym_fbo",
    "ozon_fbo": "ozon_fbo",
    "vseinstrumenti": "vseinstrumenti",
    "rework": "rework",
}
_MARKETPLACE_NAMES = {
    "wildberries": "WB",
    "ozon": "Ozon",
    "yandex": "Яндекс",
}


class _Base(DeclarativeBase):
    pass


class WarehouseProductivityRate(_Base):
    __tablename__ = "warehouse_productivity_rates"

    task_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    rate_text: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class WarehouseProductivityExclusion(_Base):
    __tablename__ = "warehouse_productivity_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "task_type",
            "task_id",
            "user_id",
            "event_date",
            name="uq_productivity_exclusion_row",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)


class WarehouseProductivityManualRow(_Base):
    __tablename__ = "warehouse_productivity_manual_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WarehouseProductivityShare(_Base):
    __tablename__ = "warehouse_productivity_shares"
    __table_args__ = (
        UniqueConstraint(
            "event_date",
            "task_type",
            "task_id",
            "source_user_id",
            "user_id",
            name="uq_productivity_share_row",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def _timezone() -> ZoneInfo:
    name = (os.getenv("WAREHOUSE_TIMEZONE") or "Asia/Novosibirsk").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Novosibirsk")


def _date_start_ts(raw: str, *, end: bool = False) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return None
    parsed = date.fromisoformat(value)
    point = datetime.combine(parsed, time.max if end else time.min, tzinfo=_timezone())
    return int(point.timestamp())


def _normalize_rate_text(raw: Any) -> str:
    value = str(raw or "").strip().replace(" ", "").replace(",", ".")
    if not value:
        return ""
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Цена должна быть числом") from exc
    if parsed < 0:
        raise ValueError("Цена не может быть отрицательной")
    if parsed > Decimal("1000000"):
        raise ValueError("Цена слишком большая")
    quantized = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return str(int(quantized))
    return f"{quantized:.2f}"


def _parse_rate(raw: str) -> Decimal | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _format_amount(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return str(int(quantized))
    return f"{quantized:.2f}"


def _pay_amount(
    quantity: int,
    task_type: str,
    counted: bool,
    rates: dict[str, Decimal | None],
) -> Decimal | None:
    if not counted or quantity <= 0:
        return None
    rate = rates.get(_RATE_KEY.get(task_type, ""))
    if rate is None:
        return None
    return rate * Decimal(quantity)


def _split_quantities(total: int, parts: int) -> list[int]:
    count = max(1, int(parts))
    qty = max(0, int(total))
    base, remainder = divmod(qty, count)
    return [base + remainder] + [base] * (count - 1)


def _sum_pay(amounts: list[Decimal | None]) -> str:
    total = Decimal("0")
    has_pay = False
    for amount in amounts:
        if amount is None:
            continue
        total += amount
        has_pay = True
    return _format_amount(total) if has_pay else ""


class WarehouseProductivityRepository:
    def __init__(self, db_url: str | None = None, *, engine=None) -> None:
        if engine is None and not db_url:
            raise ValueError("Нужен db_url или готовый engine")
        self.engine = engine or create_db_engine(str(db_url))

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)

    def get_rates(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            stored = {
                str(row.task_type): str(row.rate_text or "")
                for row in session.scalars(select(WarehouseProductivityRate))
            }
        return {
            "rates": [
                {
                    "id": key,
                    "name": name,
                    "rate": stored.get(key, ""),
                }
                for key, name in _RATE_TYPES
            ]
        }

    def save_rates(self, items: list[dict[str, Any]] | None) -> dict[str, Any]:
        incoming: dict[str, str] = {}
        for item in items or []:
            key = str((item or {}).get("id") or "").strip()
            if key not in _RATE_IDS:
                raise ValueError("Неизвестный тип задания")
            incoming[key] = _normalize_rate_text((item or {}).get("rate"))
        with Session(self.engine) as session:
            for key, _name in _RATE_TYPES:
                if key not in incoming:
                    continue
                rate_text = incoming[key]
                row = session.get(WarehouseProductivityRate, key)
                if row is None:
                    session.add(
                        WarehouseProductivityRate(task_type=key, rate_text=rate_text)
                    )
                else:
                    row.rate_text = rate_text
            session.commit()
        return self.get_rates()

    def add_manual_row(
        self,
        *,
        event_date: str,
        user_id: int,
        quantity: int,
        comment: str = "",
    ) -> dict[str, Any]:
        try:
            day = date.fromisoformat(str(event_date).strip()).isoformat()
            user_id = int(user_id)
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError("Дата, сотрудник и количество должны быть указаны корректно") from exc
        event_ts = _date_start_ts(day)
        if event_ts is None:
            raise ValueError("Дата строки обязательна")
        if user_id <= 0:
            raise ValueError("Сотрудник должен быть указан")
        if quantity <= 0:
            raise ValueError("Количество должно быть больше нуля")
        note = str(comment or "").strip()
        if len(note) > 2000:
            raise ValueError("Комментарий слишком длинный")
        now_ts = int(datetime.now(_timezone()).timestamp())
        with Session(self.engine) as session:
            target = session.execute(
                text(
                    "SELECT id, display_name, login FROM warehouse_users "
                    "WHERE id = :user_id"
                ),
                {"user_id": user_id},
            ).mappings().first()
            if target is None:
                raise ValueError("Выбранный сотрудник не найден")
            row = WarehouseProductivityManualRow(
                user_id=user_id,
                event_ts=event_ts,
                event_date=day,
                quantity=quantity,
                comment=note,
                created_at_ts=now_ts,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            task_id = int(row.id)
        employee = str(target["display_name"] or target["login"] or user_id)
        return {
            "ok": True,
            "task_type": "rework",
            "task_id": task_id,
            "date": day,
            "user_id": user_id,
            "employee": employee,
            "quantity": quantity,
            "task_data": note or "Без описания",
        }

    def set_row_counted(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        user_id: int,
        counted: bool,
    ) -> dict[str, Any]:
        try:
            date.fromisoformat(str(event_date).strip())
            task_id = int(task_id)
            user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные данные строки выработки") from exc
        if task_type not in _TYPE_NAMES:
            raise ValueError("Неизвестный тип задачи")
        if task_id <= 0 or user_id <= 0:
            raise ValueError("Задание и сотрудник должны быть указаны")
        details = self.list_details(
            event_date=str(event_date).strip(),
            task_type=task_type,
            task_id=task_id,
            user_id=user_id,
        )
        if details["row_count"] <= 0:
            raise LookupError("Строка выработки не найдена или уже была изменена")
        day = str(event_date).strip()
        with Session(self.engine) as session:
            existing = session.scalar(
                select(WarehouseProductivityExclusion).where(
                    WarehouseProductivityExclusion.task_type == task_type,
                    WarehouseProductivityExclusion.task_id == task_id,
                    WarehouseProductivityExclusion.user_id == user_id,
                    WarehouseProductivityExclusion.event_date == day,
                )
            )
            if counted:
                if existing is not None:
                    session.delete(existing)
            elif existing is None:
                session.add(
                    WarehouseProductivityExclusion(
                        task_type=task_type,
                        task_id=task_id,
                        user_id=user_id,
                        event_date=day,
                    )
                )
            session.commit()
        return {"ok": True, "counted": bool(counted)}

    def _load_rates_and_exclusions(
        self, conn
    ) -> tuple[dict[str, Decimal | None], set[tuple[str, int, int, str]]]:
        rates = {key: None for key, _name in _RATE_TYPES}
        for row in conn.execute(
            text("SELECT task_type, rate_text FROM warehouse_productivity_rates")
        ).mappings():
            key = str(row["task_type"] or "")
            if key in rates:
                rates[key] = _parse_rate(str(row["rate_text"] or ""))
        exclusions: set[tuple[str, int, int, str]] = set()
        for row in conn.execute(
            text(
                "SELECT task_type, task_id, user_id, event_date "
                "FROM warehouse_productivity_exclusions"
            )
        ).mappings():
            exclusions.add(
                (
                    str(row["task_type"]),
                    int(row["task_id"]),
                    int(row["user_id"]),
                    str(row["event_date"]),
                )
            )
        return rates, exclusions

    def _load_shares(
        self,
        conn,
        *,
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["TRUE"]
        params: dict[str, Any] = {}
        if date_from:
            clauses.append("event_date >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("event_date <= :date_to")
            params["date_to"] = date_to
        rows = conn.execute(
            text(
                "SELECT event_date, task_type, task_id, source_user_id, user_id "
                "FROM warehouse_productivity_shares WHERE "
                + " AND ".join(clauses)
            ),
            params,
        ).mappings()
        return [dict(row) for row in rows]

    def _inbound_share(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT s.source_user_id, s.user_id,
                           COALESCE(u.display_name, u.login, '') AS source_name
                    FROM warehouse_productivity_shares s
                    LEFT JOIN warehouse_users u ON u.id = s.source_user_id
                    WHERE s.event_date = :event_date AND s.task_type = :task_type
                      AND s.task_id = :task_id AND s.user_id = :user_id
                    ORDER BY s.id
                    LIMIT 1
                    """
                ),
                {
                    "event_date": str(event_date).strip(),
                    "task_type": task_type,
                    "task_id": int(task_id),
                    "user_id": int(user_id),
                },
            ).mappings().first()
        return dict(row) if row else None

    def _apply_shares(
        self,
        grouped: dict[tuple[str, int, str, int], dict[str, Any]],
        shares: list[dict[str, Any]],
        users: dict[int, dict[str, str]],
    ) -> None:
        extras_by_source: dict[tuple[str, int, str, int], list[int]] = {}
        for share in shares:
            source_key = (
                str(share["event_date"]),
                int(share["source_user_id"]),
                str(share["task_type"]),
                int(share["task_id"]),
            )
            extra_id = int(share["user_id"])
            extras_by_source.setdefault(source_key, [])
            if extra_id not in extras_by_source[source_key]:
                extras_by_source[source_key].append(extra_id)

        originals = {
            key: int(row["quantity"])
            for key, row in grouped.items()
            if row.get("has_own_work")
        }
        for source_key, extra_ids in extras_by_source.items():
            source_row = grouped.get(source_key)
            if source_row is None or not source_row.get("has_own_work"):
                continue
            extra_ids = [uid for uid in extra_ids if uid != source_key[1]]
            if not extra_ids:
                continue
            parts = _split_quantities(originals.get(source_key, 0), 1 + len(extra_ids))
            source_row["quantity"] = parts[0]
            source_names = []
            for extra_id, extra_qty in zip(extra_ids, parts[1:]):
                extra_key = (source_key[0], extra_id, source_key[2], source_key[3])
                extra_user = users.get(extra_id, {})
                extra_name = extra_user.get("display_name") or f"Сотрудник #{extra_id}"
                source_names.append({"user_id": extra_id, "display_name": extra_name})
                if extra_key in grouped:
                    grouped[extra_key]["quantity"] += extra_qty
                    continue
                source_name = source_row.get("employee") or f"Сотрудник #{source_key[1]}"
                grouped[extra_key] = {
                    "date": source_row["date"],
                    "event_ts": source_row["event_ts"],
                    "user_id": extra_id,
                    "employee": extra_name,
                    "login": extra_user.get("login") or "",
                    "quantity": extra_qty,
                    "task_type": source_row["task_type"],
                    "task_type_name": source_row["task_type_name"],
                    "task_id": source_row["task_id"],
                    "task_data": f"{source_row['task_data']} · доля от {source_name}",
                    "has_own_work": False,
                    "packer_user_ids": [],
                    "packers": [],
                    "shared_from_user_id": source_key[1],
                    "shared_from": source_name,
                }
            source_row["packer_user_ids"] = extra_ids
            source_row["packers"] = source_names

    def list_shared_packers(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        source_user_id: int,
    ) -> dict[str, Any]:
        day = date.fromisoformat(str(event_date).strip()).isoformat()
        task_type = str(task_type or "").strip()
        task_id = int(task_id)
        source_user_id = int(source_user_id)
        if task_type not in _TYPE_NAMES:
            raise ValueError("Неизвестный тип задачи")
        if task_id <= 0 or source_user_id <= 0:
            raise ValueError("Задание и сотрудник должны быть указаны")
        with Session(self.engine) as session:
            rows = session.execute(
                text(
                    """
                    SELECT s.user_id, u.display_name, u.login
                    FROM warehouse_productivity_shares s
                    JOIN warehouse_users u ON u.id = s.user_id
                    WHERE s.event_date = :event_date AND s.task_type = :task_type
                      AND s.task_id = :task_id AND s.source_user_id = :source_user_id
                    ORDER BY u.display_name, u.login
                    """
                ),
                {
                    "event_date": day,
                    "task_type": task_type,
                    "task_id": task_id,
                    "source_user_id": source_user_id,
                },
            ).mappings()
            packers = [
                {
                    "user_id": int(row["user_id"]),
                    "display_name": str(row["display_name"] or row["login"] or row["user_id"]),
                }
                for row in rows
            ]
        return {"packers": packers, "packer_user_ids": [item["user_id"] for item in packers]}

    def set_shared_packers(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        source_user_id: int,
        user_ids: list[int],
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        day = date.fromisoformat(str(event_date).strip()).isoformat()
        task_type = str(task_type or "").strip()
        task_id = int(task_id)
        source_user_id = int(source_user_id)
        created_by = int(created_by_user_id or source_user_id)
        if task_type not in _TYPE_NAMES:
            raise ValueError("Неизвестный тип задачи")
        if task_id <= 0 or source_user_id <= 0:
            raise ValueError("Задание и сотрудник должны быть указаны")
        extras: list[int] = []
        seen: set[int] = set()
        for raw in user_ids or []:
            uid = int(raw)
            if uid <= 0 or uid == source_user_id or uid in seen:
                continue
            extras.append(uid)
            seen.add(uid)
        own = self.list_details(
            event_date=day,
            task_type=task_type,
            task_id=task_id,
            user_id=source_user_id,
            include_shares=False,
        )
        if int(own.get("quantity") or 0) <= 0:
            raise ValueError("Нет собственной выработки по этому заданию")
        now_ts = int(datetime.now(_timezone()).timestamp())
        with Session(self.engine) as session:
            if extras:
                found = {
                    int(row["id"])
                    for row in session.execute(
                        text(
                            "SELECT id FROM warehouse_users WHERE id IN ("
                            + ", ".join(f":p{i}" for i in range(len(extras)))
                            + ")"
                        ),
                        {f"p{i}": uid for i, uid in enumerate(extras)},
                    ).mappings()
                }
                missing = [uid for uid in extras if uid not in found]
                if missing:
                    raise ValueError("Выбранный сотрудник не найден")
            session.execute(
                text(
                    """
                    DELETE FROM warehouse_productivity_shares
                    WHERE event_date = :event_date AND task_type = :task_type
                      AND task_id = :task_id AND source_user_id = :source_user_id
                    """
                ),
                {
                    "event_date": day,
                    "task_type": task_type,
                    "task_id": task_id,
                    "source_user_id": source_user_id,
                },
            )
            for uid in extras:
                session.add(
                    WarehouseProductivityShare(
                        event_date=day,
                        task_type=task_type,
                        task_id=task_id,
                        source_user_id=source_user_id,
                        user_id=uid,
                        created_by_user_id=created_by,
                        created_at_ts=now_ts,
                    )
                )
            session.commit()
        return self.list_shared_packers(
            event_date=day,
            task_type=task_type,
            task_id=task_id,
            source_user_id=source_user_id,
        )

    def _move_exclusion(
        self,
        conn,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        from_user_id: int,
        to_user_id: int | None = None,
        to_date: str | None = None,
    ) -> None:
        new_user_id = from_user_id if to_user_id is None else to_user_id
        new_date = event_date if to_date is None else to_date
        if new_user_id == from_user_id and new_date == event_date:
            return
        params = {
            "task_type": task_type,
            "task_id": task_id,
            "from_user_id": from_user_id,
            "event_date": event_date,
            "to_user_id": new_user_id,
            "to_date": new_date,
        }
        conn.execute(
            text(
                """
                DELETE FROM warehouse_productivity_exclusions
                WHERE task_type = :task_type AND task_id = :task_id
                  AND user_id = :to_user_id AND event_date = :to_date
                  AND NOT (
                      user_id = :from_user_id AND event_date = :event_date
                  )
                """
            ),
            params,
        )
        conn.execute(
            text(
                """
                UPDATE warehouse_productivity_exclusions
                SET user_id = :to_user_id, event_date = :to_date
                WHERE task_type = :task_type AND task_id = :task_id
                  AND user_id = :from_user_id AND event_date = :event_date
                """
            ),
            params,
        )

    def list_rows(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        f = dict(filters or {})
        try:
            date_from_ts = _date_start_ts(str(f.get("date_from") or ""))
            date_to_ts = _date_start_ts(str(f.get("date_to") or ""), end=True)
        except ValueError as exc:
            raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД") from exc
        try:
            user_id = int(f["user_id"]) if f.get("user_id") not in (None, "") else None
            min_qty = int(f["min_quantity"]) if f.get("min_quantity") not in (None, "") else None
            max_qty = int(f["max_quantity"]) if f.get("max_quantity") not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ValueError("Сотрудник и количество должны быть числами") from exc
        if min_qty is not None and min_qty < 0:
            raise ValueError("Минимальное количество не может быть отрицательным")
        if max_qty is not None and max_qty < 0:
            raise ValueError("Максимальное количество не может быть отрицательным")

        date_from = str(f.get("date_from") or "").strip()
        date_to = str(f.get("date_to") or "").strip()
        params: dict[str, Any] = {}
        if date_from_ts is not None:
            params["date_from_ts"] = date_from_ts
        if date_to_ts is not None:
            params["date_to_ts"] = date_to_ts

        sql_user_ids: list[int] | None = None
        shares: list[dict[str, Any]] = []
        with self.engine.connect() as share_conn:
            shares = self._load_shares(
                share_conn,
                date_from=date_from,
                date_to=date_to,
            )
        if user_id is not None:
            related = {user_id}
            for share in shares:
                if int(share["user_id"]) == user_id or int(share["source_user_id"]) == user_id:
                    related.add(int(share["user_id"]))
                    related.add(int(share["source_user_id"]))
            sql_user_ids = sorted(related)
            for index, uid in enumerate(sql_user_ids):
                params[f"uid{index}"] = uid

        def common(ts: str, uid: str) -> str:
            clauses = ["TRUE"]
            if date_from_ts is not None:
                clauses.append(f"{ts} >= :date_from_ts")
            if date_to_ts is not None:
                clauses.append(f"{ts} <= :date_to_ts")
            if sql_user_ids is not None:
                placeholders = ", ".join(f":uid{index}" for index in range(len(sql_user_ids)))
                clauses.append(f"{uid} IN ({placeholders})")
            return " AND ".join(clauses)
        queries = [
            (
                "fbs",
                text(
                    """
                    SELECT l.done_at_ts AS event_ts, l.done_by_user_id AS user_id,
                           j.id AS task_id, 1 AS quantity, j.marketplace,
                           j.supply_id, j.transfer_number, '' AS warehouse_name,
                           '' AS seller_name, '' AS plan_date, '' AS order_number,
                           '' AS supplier, '' AS delivery_date
                    FROM fbs_packing_lines l
                    JOIN fbs_packing_jobs j ON j.id = l.job_id
                    WHERE l.status = 'done' AND l.done_at_ts IS NOT NULL
                      AND l.done_by_user_id IS NOT NULL AND
                    """
                    + common("l.done_at_ts", "l.done_by_user_id")
                ),
            ),
            (
                "wb_fbo",
                text(
                    """
                    SELECT l.done_at_ts AS event_ts, l.done_by_user_id AS user_id,
                           j.id AS task_id, l.item_qty AS quantity, '' AS marketplace,
                           j.supply_id, '' AS transfer_number, j.warehouse_name,
                           j.seller_name, j.plan_date, '' AS order_number,
                           '' AS supplier, '' AS delivery_date
                    FROM wb_fbo_packing_lines l
                    JOIN wb_fbo_packing_jobs j ON j.id = l.job_id
                    WHERE l.status = 'done' AND l.done_at_ts IS NOT NULL
                      AND l.done_by_user_id IS NOT NULL AND
                    """
                    + common("l.done_at_ts", "l.done_by_user_id")
                ),
            ),
            (
                "wb_fbo_new",
                text(
                    """
                    SELECT i.assigned_at_ts AS event_ts, i.assigned_by_user_id AS user_id,
                           j.id AS task_id, i.item_qty AS quantity, '' AS marketplace,
                           j.supply_id, '' AS transfer_number, j.warehouse_name,
                           j.seller_name, j.plan_date, '' AS order_number,
                           '' AS supplier, '' AS delivery_date
                    FROM wb_fbo_sheet_box_items i
                    JOIN wb_fbo_sheet_jobs j ON j.id = i.job_id
                    WHERE i.item_qty > 0 AND i.assigned_at_ts IS NOT NULL
                      AND i.assigned_by_user_id IS NOT NULL AND
                    """
                    + common("i.assigned_at_ts", "i.assigned_by_user_id")
                ),
            ),
            (
                "ym_fbo",
                text(
                    """
                    SELECT i.assigned_at_ts AS event_ts, i.assigned_by_user_id AS user_id,
                           j.id AS task_id, i.quantity AS quantity, '' AS marketplace,
                           j.marketplace_request_id AS supply_id, '' AS transfer_number,
                           j.warehouse_name, '' AS seller_name, j.accept_at AS plan_date,
                           '' AS order_number, '' AS supplier, '' AS delivery_date
                    FROM yandex_fbo_cargo_items i
                    JOIN yandex_fbo_jobs j ON j.id = i.job_id
                    WHERE i.quantity > 0 AND i.assigned_at_ts IS NOT NULL
                      AND i.assigned_by_user_id IS NOT NULL AND
                    """
                    + common("i.assigned_at_ts", "i.assigned_by_user_id")
                ),
            ),
            (
                "vseinstrumenti",
                text(
                    """
                    SELECT l.done_at_ts AS event_ts, l.done_by_user_id AS user_id,
                           j.id AS task_id, l.picked_qty AS quantity, '' AS marketplace,
                           '' AS supply_id, j.transfer_number, '' AS warehouse_name,
                           '' AS seller_name, '' AS plan_date, j.order_number,
                           j.supplier, j.delivery_date
                    FROM other_marketplace_lines l
                    JOIN other_marketplace_jobs j ON j.id = l.job_id
                    WHERE j.platform = 'vseinstrumenti' AND l.status = 'done'
                      AND l.picked_qty > 0 AND l.done_at_ts IS NOT NULL
                      AND l.done_by_user_id IS NOT NULL AND
                    """
                    + common("l.done_at_ts", "l.done_by_user_id")
                ),
            ),
        ]

        raw_rows: list[dict[str, Any]] = []
        with self.engine.connect() as conn:
            users = {
                int(row["id"]): {
                    "display_name": str(row["display_name"] or row["login"] or row["id"]),
                    "login": str(row["login"] or ""),
                }
                for row in conn.execute(
                    text("SELECT id, display_name, login FROM warehouse_users")
                ).mappings()
            }
            rates, exclusions = self._load_rates_and_exclusions(conn)
            for task_type, query in queries:
                for row in conn.execute(query, params).mappings():
                    item = dict(row)
                    item["task_type"] = task_type
                    raw_rows.append(item)
            for row in conn.execute(
                text(
                    """
                    SELECT event_ts, user_id, id AS task_id, quantity, comment
                    FROM warehouse_productivity_manual_rows
                    WHERE quantity > 0 AND
                    """
                    + common("event_ts", "user_id")
                ),
                params,
            ).mappings():
                item = dict(row)
                item["task_type"] = "rework"
                raw_rows.append(item)

        grouped: dict[tuple[str, int, str, int], dict[str, Any]] = {}
        tz = _timezone()
        for item in raw_rows:
            event_ts = int(item["event_ts"])
            uid = int(item["user_id"])
            task_type = str(item["task_type"])
            task_id = int(item["task_id"])
            day = datetime.fromtimestamp(event_ts, tz).date().isoformat()
            key = (day, uid, task_type, task_id)
            if key not in grouped:
                user = users.get(uid, {})
                grouped[key] = {
                    "date": day,
                    "event_ts": event_ts,
                    "user_id": uid,
                    "employee": user.get("display_name") or f"Сотрудник #{uid}",
                    "login": user.get("login") or "",
                    "quantity": 0,
                    "task_type": task_type,
                    "task_type_name": _TYPE_NAMES[task_type],
                    "task_id": task_id,
                    "task_data": self._task_data(task_type, task_id, item),
                    "has_own_work": True,
                    "packer_user_ids": [],
                    "packers": [],
                    "shared_from_user_id": None,
                    "shared_from": "",
                }
            grouped[key]["quantity"] += max(0, int(item.get("quantity") or 0))
            grouped[key]["event_ts"] = max(grouped[key]["event_ts"], event_ts)
            grouped[key]["has_own_work"] = True
            grouped[key]["packer_user_ids"] = []
            grouped[key]["packers"] = []
            grouped[key]["shared_from_user_id"] = None
            grouped[key]["shared_from"] = ""

        self._apply_shares(grouped, shares, users)

        for key, row in grouped.items():
            day, uid, task_type, task_id = key
            counted = (task_type, task_id, uid, day) not in exclusions
            amount = _pay_amount(int(row["quantity"]), task_type, counted, rates)
            row["counted"] = counted
            row["pay"] = _format_amount(amount) if amount is not None else ""

        task_type_filter = str(f.get("task_type") or "").strip()
        task_query = str(f.get("task_query") or "").strip().casefold()
        q = str(f.get("q") or "").strip().casefold()
        rows = []
        for row in grouped.values():
            if user_id is not None and int(row["user_id"]) != user_id:
                continue
            if task_type_filter and row["task_type"] != task_type_filter:
                continue
            if min_qty is not None and row["quantity"] < min_qty:
                continue
            if max_qty is not None and row["quantity"] > max_qty:
                continue
            if task_query and task_query not in row["task_data"].casefold():
                continue
            if q:
                haystack = " ".join(
                    [
                        row["date"],
                        row["employee"],
                        row["login"],
                        row["task_type_name"],
                        row["task_data"],
                        str(row["quantity"]),
                        str(row.get("pay") or ""),
                    ]
                ).casefold()
                if q not in haystack:
                    continue
            rows.append(row)
        rows.sort(key=lambda row: (row["date"], row["event_ts"], row["employee"]), reverse=True)
        counted_rows = [row for row in rows if row.get("counted", True)]
        return {
            "rows": rows,
            "total_quantity": sum(int(row["quantity"]) for row in counted_rows),
            "total_pay": _sum_pay(
                [
                    Decimal(row["pay"]) if str(row.get("pay") or "") else None
                    for row in counted_rows
                ]
            ),
            "row_count": len(rows),
            "task_types": [
                {"id": key, "name": value} for key, value in _TYPE_NAMES.items()
            ],
        }

    def list_user_month(
        self,
        *,
        user_id: int,
        year: int | None = None,
        month: int | None = None,
    ) -> dict[str, Any]:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный сотрудник") from exc
        if user_id <= 0:
            raise ValueError("Некорректный сотрудник")

        now = datetime.now(_timezone())
        year_n = int(year or now.year)
        month_n = int(month or now.month)
        if year_n < 2000 or year_n > 2100 or month_n < 1 or month_n > 12:
            raise ValueError("Некорректный месяц")

        last_day = monthrange(year_n, month_n)[1]
        result = self.list_rows(
            {
                "user_id": str(user_id),
                "date_from": f"{year_n:04d}-{month_n:02d}-01",
                "date_to": f"{year_n:04d}-{month_n:02d}-{last_day:02d}",
            }
        )
        grouped_days: dict[str, dict[str, Any]] = {}
        for row in result["rows"]:
            if not row.get("counted", True):
                continue
            event_date = str(row["date"])
            day = grouped_days.setdefault(
                event_date,
                {
                    "date": event_date,
                    "display_date": datetime.strptime(event_date, "%Y-%m-%d").strftime(
                        "%d.%m.%Y"
                    ),
                    "quantity": 0,
                    "pay": "",
                    "tasks": [],
                    "_pay_amounts": [],
                },
            )
            quantity = max(0, int(row["quantity"]))
            day["quantity"] += quantity
            pay_text = str(row.get("pay") or "")
            day["_pay_amounts"].append(Decimal(pay_text) if pay_text else None)
            day["tasks"].append(
                {
                    "task_type": row["task_type"],
                    "task_type_name": row["task_type_name"],
                    "task_id": int(row["task_id"]),
                    "task_data": row["task_data"],
                    "quantity": quantity,
                    "pay": pay_text,
                    "has_own_work": bool(row.get("has_own_work", True)),
                    "packer_user_ids": list(row.get("packer_user_ids") or []),
                    "packers": list(row.get("packers") or []),
                    "shared_from_user_id": row.get("shared_from_user_id"),
                    "shared_from": row.get("shared_from") or "",
                }
            )

        days = sorted(grouped_days.values(), key=lambda item: item["date"], reverse=True)
        for day in days:
            day["pay"] = _sum_pay(day.pop("_pay_amounts"))
        return {
            "month": f"{year_n:04d}-{month_n:02d}",
            "total_quantity": sum(int(day["quantity"]) for day in days),
            "total_pay": _sum_pay(
                [Decimal(day["pay"]) if day.get("pay") else None for day in days]
            ),
            "days": days,
        }

    def list_details(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        user_id: int,
        include_shares: bool = True,
    ) -> dict[str, Any]:
        try:
            date_from_ts = _date_start_ts(event_date)
            date_to_ts = _date_start_ts(event_date, end=True)
            task_id = int(task_id)
            user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные параметры детализации") from exc
        if date_from_ts is None or date_to_ts is None:
            raise ValueError("Дата строки обязательна")
        if task_id <= 0 or user_id <= 0:
            raise ValueError("Задание и сотрудник должны быть указаны")

        queries = {
            "fbs": text(
                """
                SELECT l.id AS line_id, l.done_at_ts AS event_ts, 1 AS quantity,
                       l.order_id AS reference, l.place_index, l.place_total,
                       l.sku, l.product_name
                FROM fbs_packing_lines l
                WHERE l.job_id = :task_id AND l.status = 'done'
                  AND l.done_by_user_id = :user_id
                  AND l.done_at_ts >= :date_from_ts AND l.done_at_ts <= :date_to_ts
                ORDER BY l.done_at_ts DESC, l.id DESC
                """
            ),
            "wb_fbo": text(
                """
                SELECT l.id AS line_id, l.done_at_ts AS event_ts,
                       l.item_qty AS quantity, l.box_human_id AS reference,
                       1 AS place_index, 1 AS place_total, l.sku, l.product_name
                FROM wb_fbo_packing_lines l
                WHERE l.job_id = :task_id AND l.status = 'done'
                  AND l.done_by_user_id = :user_id
                  AND l.done_at_ts >= :date_from_ts AND l.done_at_ts <= :date_to_ts
                ORDER BY l.done_at_ts DESC, l.id DESC
                """
            ),
            "wb_fbo_new": text(
                """
                SELECT i.id AS line_id, i.assigned_at_ts AS event_ts,
                       i.item_qty AS quantity, b.box_human_id AS reference,
                       1 AS place_index, 1 AS place_total,
                       COALESCE((
                           SELECT p.sku FROM wb_fbo_sheet_products p
                           WHERE p.job_id = i.job_id
                             AND p.barcode = i.product_barcode
                           ORDER BY p.id LIMIT 1
                       ), i.product_barcode, '') AS sku,
                       COALESCE((
                           SELECT p.name FROM wb_fbo_sheet_products p
                           WHERE p.job_id = i.job_id
                             AND p.barcode = i.product_barcode
                           ORDER BY p.id LIMIT 1
                       ), '') AS product_name
                FROM wb_fbo_sheet_box_items i
                JOIN wb_fbo_sheet_boxes b ON b.id = i.box_id
                WHERE i.job_id = :task_id AND i.item_qty > 0
                  AND i.assigned_by_user_id = :user_id
                  AND i.assigned_at_ts >= :date_from_ts
                  AND i.assigned_at_ts <= :date_to_ts
                ORDER BY i.assigned_at_ts DESC, i.id DESC
                """
            ),
            "ym_fbo": text(
                """
                SELECT i.id AS line_id, i.assigned_at_ts AS event_ts,
                       i.quantity AS quantity, c.cargo_code AS reference,
                       1 AS place_index, 1 AS place_total,
                       p.sku, p.product_name
                FROM yandex_fbo_cargo_items i
                JOIN yandex_fbo_cargoes c ON c.id = i.cargo_id
                JOIN yandex_fbo_products p ON p.id = i.product_id
                WHERE i.job_id = :task_id AND i.quantity > 0
                  AND i.assigned_by_user_id = :user_id
                  AND i.assigned_at_ts >= :date_from_ts
                  AND i.assigned_at_ts <= :date_to_ts
                ORDER BY i.assigned_at_ts DESC, i.id DESC
                """
            ),
            "vseinstrumenti": text(
                """
                SELECT l.id AS line_id, l.done_at_ts AS event_ts,
                       l.picked_qty AS quantity, j.order_number AS reference,
                       1 AS place_index, 1 AS place_total, l.sku, l.product_name
                FROM other_marketplace_lines l
                JOIN other_marketplace_jobs j ON j.id = l.job_id
                WHERE l.job_id = :task_id AND j.platform = 'vseinstrumenti'
                  AND l.status = 'done' AND l.picked_qty > 0
                  AND l.done_by_user_id = :user_id
                  AND l.done_at_ts >= :date_from_ts AND l.done_at_ts <= :date_to_ts
                ORDER BY l.done_at_ts DESC, l.id DESC
                """
            ),
            "rework": text(
                """
                SELECT id AS line_id, event_ts, quantity,
                       comment AS reference, 1 AS place_index, 1 AS place_total,
                       '' AS sku, comment AS product_name
                FROM warehouse_productivity_manual_rows
                WHERE id = :task_id AND user_id = :user_id
                  AND event_ts >= :date_from_ts AND event_ts <= :date_to_ts
                ORDER BY id DESC
                """
            ),
        }
        if task_type not in queries:
            raise ValueError("Неизвестный тип задачи")

        params = {
            "date_from_ts": date_from_ts,
            "date_to_ts": date_to_ts,
            "task_id": task_id,
            "user_id": user_id,
        }
        tz = _timezone()
        details = []
        with self.engine.connect() as conn:
            for row in conn.execute(queries[task_type], params).mappings():
                event_ts = int(row["event_ts"])
                reference = str(row.get("reference") or "").strip()
                if (
                    task_type == "fbs"
                    and reference
                    and int(row.get("place_total") or 1) > 1
                ):
                    reference = (
                        f"{reference} "
                        f"{int(row.get('place_index') or 1)}/"
                        f"{int(row.get('place_total') or 1)}"
                    )
                details.append(
                    {
                        "line_id": int(row["line_id"]),
                        "event_ts": event_ts,
                        "completed_at": datetime.fromtimestamp(event_ts, tz).strftime(
                            "%d.%m.%Y %H:%M:%S"
                        ),
                        "reference": reference,
                        "sku": str(row.get("sku") or "").strip(),
                        "product_name": str(row.get("product_name") or "").strip(),
                        "quantity": max(0, int(row.get("quantity") or 0)),
                    }
                )
        payload = {
            "details": details,
            "quantity": sum(item["quantity"] for item in details),
            "row_count": len(details),
        }
        if payload["quantity"] > 0 or not include_shares:
            return payload
        share = self._inbound_share(
            event_date=str(event_date).strip(),
            task_type=task_type,
            task_id=task_id,
            user_id=user_id,
        )
        if share is None:
            return payload
        extras = self.list_shared_packers(
            event_date=str(event_date).strip(),
            task_type=task_type,
            task_id=task_id,
            source_user_id=int(share["source_user_id"]),
        )
        source_qty = int(
            self.list_details(
                event_date=str(event_date).strip(),
                task_type=task_type,
                task_id=task_id,
                user_id=int(share["source_user_id"]),
                include_shares=False,
            )["quantity"]
        )
        extra_ids = list(extras.get("packer_user_ids") or [])
        parts = _split_quantities(source_qty, 1 + len(extra_ids))
        try:
            extra_index = extra_ids.index(int(user_id))
        except ValueError:
            return payload
        share_qty = parts[extra_index + 1]
        source_name = str(share.get("source_name") or share["source_user_id"])
        return {
            "details": [
                {
                    "line_id": 0,
                    "event_ts": date_from_ts or 0,
                    "completed_at": datetime.fromtimestamp(
                        date_from_ts or 0, tz
                    ).strftime("%d.%m.%Y %H:%M:%S")
                    if date_from_ts
                    else "",
                    "reference": f"Доля выработки от {source_name}",
                    "sku": "",
                    "product_name": f"Доля выработки от {source_name}",
                    "quantity": share_qty,
                }
            ],
            "quantity": share_qty,
            "row_count": 1,
            "shared": True,
            "shared_from_user_id": int(share["source_user_id"]),
            "shared_from": source_name,
        }

    def reassign_row(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        from_user_id: int,
        to_user_id: int,
    ) -> dict[str, Any]:
        try:
            date_from_ts = _date_start_ts(event_date)
            date_to_ts = _date_start_ts(event_date, end=True)
            task_id = int(task_id)
            from_user_id = int(from_user_id)
            to_user_id = int(to_user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные данные строки выработки") from exc
        if date_from_ts is None or date_to_ts is None:
            raise ValueError("Дата строки обязательна")
        if task_id <= 0 or from_user_id <= 0 or to_user_id <= 0:
            raise ValueError("Задание и сотрудники должны быть указаны")
        if from_user_id == to_user_id:
            raise ValueError("Этот сотрудник уже назначен")

        updates = {
            "fbs": text(
                """
                UPDATE fbs_packing_lines
                SET done_by_user_id = :to_user_id
                WHERE job_id = :task_id AND status = 'done'
                  AND done_by_user_id = :from_user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                """
            ),
            "wb_fbo": text(
                """
                UPDATE wb_fbo_packing_lines
                SET done_by_user_id = :to_user_id
                WHERE job_id = :task_id AND status = 'done'
                  AND done_by_user_id = :from_user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                """
            ),
            "wb_fbo_new": text(
                """
                UPDATE wb_fbo_sheet_box_items
                SET assigned_by_user_id = :to_user_id
                WHERE job_id = :task_id AND item_qty > 0
                  AND assigned_by_user_id = :from_user_id
                  AND assigned_at_ts >= :date_from_ts AND assigned_at_ts <= :date_to_ts
                """
            ),
            "ym_fbo": text(
                """
                UPDATE yandex_fbo_cargo_items
                SET assigned_by_user_id = :to_user_id
                WHERE job_id = :task_id AND quantity > 0
                  AND assigned_by_user_id = :from_user_id
                  AND assigned_at_ts >= :date_from_ts AND assigned_at_ts <= :date_to_ts
                """
            ),
            "vseinstrumenti": text(
                """
                UPDATE other_marketplace_lines
                SET done_by_user_id = :to_user_id
                WHERE job_id = :task_id AND status = 'done' AND picked_qty > 0
                  AND done_by_user_id = :from_user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                  AND EXISTS (
                      SELECT 1 FROM other_marketplace_jobs j
                      WHERE j.id = other_marketplace_lines.job_id
                        AND j.platform = 'vseinstrumenti'
                  )
                """
            ),
            "rework": text(
                """
                UPDATE warehouse_productivity_manual_rows
                SET user_id = :to_user_id
                WHERE id = :task_id AND user_id = :from_user_id
                  AND event_ts >= :date_from_ts AND event_ts <= :date_to_ts
                """
            ),
        }
        if task_type not in updates:
            raise ValueError("Неизвестный тип задачи")

        params = {
            "date_from_ts": date_from_ts,
            "date_to_ts": date_to_ts,
            "task_id": task_id,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
        }
        with self.engine.begin() as conn:
            target = conn.execute(
                text(
                    "SELECT id, display_name, login FROM warehouse_users "
                    "WHERE id = :to_user_id"
                ),
                {"to_user_id": to_user_id},
            ).mappings().first()
            if target is None:
                raise ValueError("Выбранный сотрудник не найден")
            result = conn.execute(updates[task_type], params)
            updated_count = int(result.rowcount or 0)
            if updated_count <= 0:
                raise LookupError("Строка выработки не найдена или уже была изменена")
            if task_type == "wb_fbo_new":
                conn.execute(
                    text(
                        """
                        UPDATE wb_fbo_sheet_boxes b
                        SET assigned_by_user_id = :to_user_id
                        WHERE b.job_id = :task_id
                          AND b.assigned_by_user_id = :from_user_id
                          AND b.assigned_at_ts >= :date_from_ts
                          AND b.assigned_at_ts <= :date_to_ts
                          AND EXISTS (
                              SELECT 1 FROM wb_fbo_sheet_box_items i
                              WHERE i.box_id = b.id
                                AND i.assigned_by_user_id = :to_user_id
                          )
                        """
                    ),
                    params,
                )
            self._move_exclusion(
                conn,
                event_date=str(event_date).strip(),
                task_type=task_type,
                task_id=task_id,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
            )
            conn.execute(
                text(
                    """
                    DELETE FROM warehouse_productivity_shares
                    WHERE event_date = :event_date AND task_type = :task_type
                      AND task_id = :task_id
                      AND (
                          (source_user_id = :from_user_id AND user_id = :to_user_id)
                          OR (source_user_id = :to_user_id AND user_id = :from_user_id)
                      )
                    """
                ),
                {
                    "event_date": str(event_date).strip(),
                    "task_type": task_type,
                    "task_id": task_id,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE warehouse_productivity_shares
                    SET source_user_id = :to_user_id
                    WHERE event_date = :event_date AND task_type = :task_type
                      AND task_id = :task_id AND source_user_id = :from_user_id
                    """
                ),
                {
                    "event_date": str(event_date).strip(),
                    "task_type": task_type,
                    "task_id": task_id,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE warehouse_productivity_shares
                    SET user_id = :to_user_id
                    WHERE event_date = :event_date AND task_type = :task_type
                      AND task_id = :task_id AND user_id = :from_user_id
                    """
                ),
                {
                    "event_date": str(event_date).strip(),
                    "task_type": task_type,
                    "task_id": task_id,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                },
            )
        employee = str(target["display_name"] or target["login"] or to_user_id)
        return {
            "ok": True,
            "updated_count": updated_count,
            "user_id": to_user_id,
            "employee": employee,
        }

    def reschedule_row(
        self,
        *,
        event_date: str,
        task_type: str,
        task_id: int,
        user_id: int,
        to_date: str,
    ) -> dict[str, Any]:
        try:
            date_from_ts = _date_start_ts(event_date)
            date_to_ts = _date_start_ts(event_date, end=True)
            new_from_ts = _date_start_ts(to_date)
            task_id = int(task_id)
            user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные данные строки выработки") from exc
        if date_from_ts is None or date_to_ts is None or new_from_ts is None:
            raise ValueError("Текущая и новая даты обязательны")
        if task_id <= 0 or user_id <= 0:
            raise ValueError("Задание и сотрудник должны быть указаны")
        if new_from_ts == date_from_ts:
            raise ValueError("Укажите другую дату")
        delta_ts = new_from_ts - date_from_ts

        updates = {
            "fbs": text(
                """
                UPDATE fbs_packing_lines
                SET done_at_ts = done_at_ts + :delta_ts
                WHERE job_id = :task_id AND status = 'done'
                  AND done_by_user_id = :user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                """
            ),
            "wb_fbo": text(
                """
                UPDATE wb_fbo_packing_lines
                SET done_at_ts = done_at_ts + :delta_ts
                WHERE job_id = :task_id AND status = 'done'
                  AND done_by_user_id = :user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                """
            ),
            "wb_fbo_new": text(
                """
                UPDATE wb_fbo_sheet_box_items
                SET assigned_at_ts = assigned_at_ts + :delta_ts
                WHERE job_id = :task_id AND item_qty > 0
                  AND assigned_by_user_id = :user_id
                  AND assigned_at_ts >= :date_from_ts AND assigned_at_ts <= :date_to_ts
                """
            ),
            "ym_fbo": text(
                """
                UPDATE yandex_fbo_cargo_items
                SET assigned_at_ts = assigned_at_ts + :delta_ts
                WHERE job_id = :task_id AND quantity > 0
                  AND assigned_by_user_id = :user_id
                  AND assigned_at_ts >= :date_from_ts AND assigned_at_ts <= :date_to_ts
                """
            ),
            "vseinstrumenti": text(
                """
                UPDATE other_marketplace_lines
                SET done_at_ts = done_at_ts + :delta_ts
                WHERE job_id = :task_id AND status = 'done' AND picked_qty > 0
                  AND done_by_user_id = :user_id
                  AND done_at_ts >= :date_from_ts AND done_at_ts <= :date_to_ts
                  AND EXISTS (
                      SELECT 1 FROM other_marketplace_jobs j
                      WHERE j.id = other_marketplace_lines.job_id
                        AND j.platform = 'vseinstrumenti'
                  )
                """
            ),
            "rework": text(
                """
                UPDATE warehouse_productivity_manual_rows
                SET event_ts = event_ts + :delta_ts,
                    event_date = :to_date
                WHERE id = :task_id AND user_id = :user_id
                  AND event_ts >= :date_from_ts AND event_ts <= :date_to_ts
                """
            ),
        }
        if task_type not in updates:
            raise ValueError("Неизвестный тип задачи")

        params = {
            "date_from_ts": date_from_ts,
            "date_to_ts": date_to_ts,
            "delta_ts": delta_ts,
            "task_id": task_id,
            "user_id": user_id,
            "to_date": str(to_date).strip(),
        }
        with self.engine.begin() as conn:
            result = conn.execute(updates[task_type], params)
            updated_count = int(result.rowcount or 0)
            if updated_count <= 0:
                raise LookupError("Строка выработки не найдена или уже была изменена")
            if task_type == "wb_fbo_new":
                conn.execute(
                    text(
                        """
                        UPDATE wb_fbo_sheet_boxes
                        SET assigned_at_ts = assigned_at_ts + :delta_ts
                        WHERE job_id = :task_id
                          AND assigned_by_user_id = :user_id
                          AND assigned_at_ts >= :date_from_ts
                          AND assigned_at_ts <= :date_to_ts
                        """
                    ),
                    params,
                )
            self._move_exclusion(
                conn,
                event_date=str(event_date).strip(),
                task_type=task_type,
                task_id=task_id,
                from_user_id=user_id,
                to_date=str(to_date).strip(),
            )
            conn.execute(
                text(
                    """
                    UPDATE warehouse_productivity_shares
                    SET event_date = :to_date
                    WHERE event_date = :event_date AND task_type = :task_type
                      AND task_id = :task_id
                      AND (source_user_id = :user_id OR user_id = :user_id)
                    """
                ),
                {
                    "event_date": str(event_date).strip(),
                    "to_date": str(to_date).strip(),
                    "task_type": task_type,
                    "task_id": task_id,
                    "user_id": user_id,
                },
            )
        return {
            "ok": True,
            "updated_count": updated_count,
            "date": str(to_date).strip(),
        }

    @staticmethod
    def _task_data(task_type: str, task_id: int, item: dict[str, Any]) -> str:
        parts = [f"Задание #{task_id}"]
        if task_type == "rework":
            note = str(item.get("comment") or "").strip()
            return note or "Без описания"
        if task_type == "fbs":
            mp = _MARKETPLACE_NAMES.get(str(item.get("marketplace") or ""), "")
            if mp:
                parts.append(mp)
            if item.get("supply_id"):
                parts.append(f"поставка {item['supply_id']}")
            if item.get("transfer_number"):
                parts.append(f"перемещение {item['transfer_number']}")
        elif task_type in {"wb_fbo", "wb_fbo_new", "ym_fbo"}:
            if item.get("supply_id"):
                parts.append(f"поставка {item['supply_id']}")
            if item.get("warehouse_name"):
                parts.append(str(item["warehouse_name"]))
            if item.get("seller_name"):
                parts.append(str(item["seller_name"]))
            if item.get("plan_date"):
                parts.append(f"план {item['plan_date']}")
        else:
            if item.get("order_number"):
                parts.append(f"заказ {item['order_number']}")
            if item.get("transfer_number"):
                parts.append(f"перемещение {item['transfer_number']}")
            if item.get("supplier"):
                parts.append(str(item["supplier"]))
            if item.get("delivery_date"):
                parts.append(f"доставка {item['delivery_date']}")
        return " · ".join(parts)
