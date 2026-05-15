"""Применение перемещения из Google Sheets + запись в журнал."""

from __future__ import annotations

import time
from typing import Any

from app.movement_repository import MovementRepository
from app.repositories import InventoryRepository
from app.sheet_import import import_movement_from_google_sheet


def execute_movement_from_sheet(
    inventory_repo: InventoryRepository,
    movement_repo: MovementRepository,
    *,
    sign: int,
    sheet_url: str,
    source: str,
) -> dict[str, Any]:
    """
    sign: +1 приход, -1 расход.
    Возвращает словарь с полями ok, error?, qty_by_sku, warnings, updated, movement_id, ...
    """
    if sign not in (1, -1):
        return {"ok": False, "error": "invalid_sign"}

    try:
        qty_by_sku, warnings = import_movement_from_google_sheet(sheet_url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if not qty_by_sku:
        return {
            "ok": False,
            "error": "no_rows",
            "warnings": warnings,
        }

    direction = "in" if sign > 0 else "out"
    deltas = {sku: sign * qty for sku, qty in qty_by_sku.items()}
    updated = inventory_repo.apply_stock_movements(deltas)
    lines = [(sku, qty, sign * qty) for sku, qty in qty_by_sku.items()]
    movement_id = movement_repo.create_movement(
        created_at_ts=int(time.time()),
        direction=direction,
        source=source,
        sheet_url=sheet_url,
        lines=lines,
        warnings=warnings,
    )
    return {
        "ok": True,
        "movement_id": movement_id,
        "updated": updated,
        "sku_count": len(qty_by_sku),
        "total_quantity": sum(qty_by_sku.values()),
        "direction": direction,
        "warnings": warnings,
    }
