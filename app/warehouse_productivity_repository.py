"""Краткий отчёт по выработке упаковщиков из завершённых строк заданий."""

from __future__ import annotations

import os
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import create_db_engine


_TYPE_NAMES = {
    "fbs": "FBS",
    "wb_fbo": "FBO WB",
    "wb_fbo_new": "FBO WB new",
    "vseinstrumenti": "ВсеИнструменты",
}
_MARKETPLACE_NAMES = {
    "wildberries": "WB",
    "ozon": "Ozon",
    "yandex": "Яндекс",
}


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


class WarehouseProductivityRepository:
    def __init__(self, db_url: str | None = None, *, engine=None) -> None:
        if engine is None and not db_url:
            raise ValueError("Нужен db_url или готовый engine")
        self.engine = engine or create_db_engine(str(db_url))

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

        params: dict[str, Any] = {}
        if date_from_ts is not None:
            params["date_from_ts"] = date_from_ts
        if date_to_ts is not None:
            params["date_to_ts"] = date_to_ts
        if user_id is not None:
            params["user_id"] = user_id

        def common(ts: str, uid: str) -> str:
            clauses = ["TRUE"]
            if date_from_ts is not None:
                clauses.append(f"{ts} >= :date_from_ts")
            if date_to_ts is not None:
                clauses.append(f"{ts} <= :date_to_ts")
            if user_id is not None:
                clauses.append(f"{uid} = :user_id")
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
            for task_type, query in queries:
                for row in conn.execute(query, params).mappings():
                    item = dict(row)
                    item["task_type"] = task_type
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
                }
            grouped[key]["quantity"] += max(0, int(item.get("quantity") or 0))
            grouped[key]["event_ts"] = max(grouped[key]["event_ts"], event_ts)

        task_type_filter = str(f.get("task_type") or "").strip()
        task_query = str(f.get("task_query") or "").strip().casefold()
        q = str(f.get("q") or "").strip().casefold()
        rows = []
        for row in grouped.values():
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
                    ]
                ).casefold()
                if q not in haystack:
                    continue
            rows.append(row)
        rows.sort(key=lambda row: (row["date"], row["event_ts"], row["employee"]), reverse=True)
        return {
            "rows": rows,
            "total_quantity": sum(int(row["quantity"]) for row in rows),
            "row_count": len(rows),
            "task_types": [
                {"id": key, "name": value} for key, value in _TYPE_NAMES.items()
            ],
        }

    @staticmethod
    def _task_data(task_type: str, task_id: int, item: dict[str, Any]) -> str:
        parts = [f"Задание #{task_id}"]
        if task_type == "fbs":
            mp = _MARKETPLACE_NAMES.get(str(item.get("marketplace") or ""), "")
            if mp:
                parts.append(mp)
            if item.get("supply_id"):
                parts.append(f"поставка {item['supply_id']}")
            if item.get("transfer_number"):
                parts.append(f"перемещение {item['transfer_number']}")
        elif task_type in {"wb_fbo", "wb_fbo_new"}:
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
