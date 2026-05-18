"""Журнал перемещений после отгрузки FBS (/ship_*)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.movement_repository import MovementRepository

_SOURCE_LABELS: dict[str, str] = {
    "ozon": "Ozon",
    "wildberries": "WB",
    "yandex_market": "Yandex",
}

_MAX_COMMENT_LEN = 4096


def fbs_ship_movement_title(source: str, created_at_ts: int) -> str:
    mp = _SOURCE_LABELS.get(source, source)
    dt = datetime.fromtimestamp(int(created_at_ts), tz=timezone.utc)
    return f"FBS {mp} {dt.strftime('%d.%m.%Y')}"


def record_fbs_ship_movement(
    movement_repo: MovementRepository,
    *,
    source: str,
    external_order_ids: list[str],
    qty_by_sku: dict[str, int],
) -> int | None:
    """
    Запись расхода в журнал (остатки уже списаны в ship_active_reserves_by_external_ids).
    """
    lines = [(sku, int(qty), -int(qty)) for sku, qty in qty_by_sku.items() if int(qty) > 0]
    if not lines:
        return None

    ts = int(time.time())
    title = fbs_ship_movement_title(source, ts)

    seen: set[str] = set()
    orders: list[str] = []
    for oid in external_order_ids:
        o = str(oid).strip()
        if not o or o in seen:
            continue
        seen.add(o)
        orders.append(o)

    comment = ", ".join(orders)
    warnings: list[str] = []
    if len(comment) > _MAX_COMMENT_LEN:
        warnings.append(f"Список заказов обрезан (всего {len(orders)}).")
        comment = comment[:_MAX_COMMENT_LEN]

    return movement_repo.create_movement(
        created_at_ts=ts,
        direction="out",
        source="telegram",
        sheet_url="",
        lines=lines,
        warnings=warnings,
        title=title,
        comment=comment,
    )
