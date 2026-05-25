"""FBS-отгрузка резервов по статусам МП (общая логика для бота /ship_* и веб-панели)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.adapters.ozon import OzonAdapter
from app.adapters.wildberries import WildberriesAdapter
from app.adapters.yandex_market import YandexMarketAdapter
from app.movement_repository import MovementRepository
from app.repositories import InventoryRepository
from app.services import StockCoordinator
from app.ship_movement import record_fbs_ship_movement

logger = logging.getLogger(__name__)

FBS_SHIP_SCOPES: dict[str, frozenset[str]] = {
    "all": frozenset({"ozon", "yandex_market", "wildberries"}),
    "ozon": frozenset({"ozon"}),
    "wildberries": frozenset({"wildberries"}),
    "yandex_market": frozenset({"yandex_market"}),
}

_SCOPE_LABELS: dict[str, str] = {
    "all": "все МП",
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_market": "Яндекс Маркет",
}

_SOURCE_LABELS: dict[str, str] = {
    "ozon": "Ozon",
    "wildberries": "WB",
    "yandex_market": "Yandex",
}


def normalize_ship_scope(scope: str) -> str:
    key = (scope or "").strip().lower()
    if key in ("ship_all", "all"):
        return "all"
    if key in FBS_SHIP_SCOPES:
        return key
    raise ValueError(f"Неизвестный scope: {scope}")


def scope_label(scope: str) -> str:
    return _SCOPE_LABELS.get(scope, scope)


def sources_for_scope(scope: str) -> frozenset[str]:
    return FBS_SHIP_SCOPES[normalize_ship_scope(scope)]


def _ids_to_ship(
    inventory_repo: InventoryRepository,
    adapter: Any,
    src: str,
) -> tuple[set[str], int]:
    """Возвращает (external_ids к отгрузке, число активных резервов added)."""
    active_external_ids = inventory_repo.get_active_reserve_external_ids(src)
    active_count = len(active_external_ids)
    if not active_external_ids:
        return set(), active_count

    if isinstance(adapter, WildberriesAdapter):
        ready_external_ids = adapter.fetch_ready_to_ship_external_ids(active_external_ids)
        return set(active_external_ids) & set(ready_external_ids), active_count

    if isinstance(adapter, (OzonAdapter, YandexMarketAdapter)):
        ready_external_ids = adapter.fetch_ready_to_ship_external_ids()
        if isinstance(adapter, YandexMarketAdapter):
            started_order_ids: set[str] = set()
            for ext in ready_external_ids:
                order_id = str(ext).split(":", 1)[0].strip()
                if order_id:
                    started_order_ids.add(order_id)
            ids_to_ship: set[str] = set()
            for ext in active_external_ids:
                order_id = str(ext).split(":", 1)[0].strip()
                if not order_id or order_id not in started_order_ids:
                    ids_to_ship.add(ext)
            return ids_to_ship, active_count
        return set(active_external_ids) & set(ready_external_ids), active_count

    return set(), active_count


def preview_fbs_ship(
    inventory_repo: InventoryRepository,
    coordinator: StockCoordinator,
    sources: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Оценка отгрузки без синка и без списания."""
    adapters_by_name = {a.name: a for a in coordinator.adapters if a.is_configured()}
    by_source: dict[str, dict[str, int]] = {}
    for src in sorted(sources):
        adapter = adapters_by_name.get(src)
        if adapter is None:
            by_source[src] = {
                "configured": False,
                "active_reserves": 0,
                "ready_to_ship": 0,
            }
            continue
        try:
            ids_to_ship, active_count = _ids_to_ship(inventory_repo, adapter, src)
            by_source[src] = {
                "configured": True,
                "active_reserves": active_count,
                "ready_to_ship": len(ids_to_ship),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ship preview failed for source=%s", src)
            by_source[src] = {
                "configured": True,
                "active_reserves": 0,
                "ready_to_ship": 0,
                "error": str(exc),
            }
    return {"by_source": by_source}


def execute_fbs_ship(
    inventory_repo: InventoryRepository,
    coordinator: StockCoordinator,
    movement_repo: MovementRepository,
    sources: set[str] | frozenset[str],
    *,
    sync_before: bool = True,
    journal_source: str = "telegram",
) -> dict[str, Any]:
    """Синк (опционально), отгрузка по статусам МП, пуш остатков на МП, запись в журнал."""
    sync_result: dict[str, Any] = {"ok": True, "adapter_errors": []}
    if sync_before:
        sync_result = coordinator.sync_cycle()

    adapters_by_name = {a.name: a for a in coordinator.adapters if a.is_configured()}
    by_source_out: dict[str, dict[str, int]] = {}
    source_errors: list[str] = []
    movement_ids: dict[str, int] = {}

    for src in sorted(sources):
        adapter = adapters_by_name.get(src)
        if adapter is None:
            continue
        try:
            ids_to_ship, _active_count = _ids_to_ship(inventory_repo, adapter, src)
            stats = inventory_repo.ship_active_reserves_by_external_ids(src, ids_to_ship)
            by_source_out[src] = {
                "reserves_shipped": int(stats.get("reserves_shipped", 0)),
                "reserved_units": int(stats.get("reserved_units", 0)),
                "affected_skus": int(stats.get("affected_skus", 0)),
            }
            if int(stats.get("reserves_shipped", 0)) > 0:
                mid = record_fbs_ship_movement(
                    movement_repo,
                    source=src,
                    external_order_ids=list(stats.get("external_order_ids") or []),
                    qty_by_sku=dict(stats.get("qty_by_sku") or {}),
                    journal_source=journal_source,
                )
                if mid is not None:
                    movement_ids[src] = mid
        except Exception as exc:  # noqa: BLE001
            source_errors.append(f"{src}: ship failed ({exc})")
            logger.exception("Ship failed for source=%s", src)
            by_source_out[src] = {
                "reserves_shipped": 0,
                "reserved_units": 0,
                "affected_skus": 0,
            }

    available = inventory_repo.get_available_stock_map()
    now_ts = int(time.time())
    for adapter in coordinator.adapters:
        if adapter.is_configured():
            changed_available = inventory_repo.get_adapter_stock_push_delta(adapter.name, available)
            if not changed_available:
                continue
            adapter.sync_available_stock(changed_available)
            inventory_repo.mark_adapter_stock_push_applied(adapter.name, changed_available, now_ts)

    coordinator.last_run_at = None
    coordinator.last_error = None
    coordinator.last_warnings = []

    total_reserves_shipped = sum(int(st.get("reserves_shipped", 0)) for st in by_source_out.values())
    total_reserved_units = sum(int(st.get("reserved_units", 0)) for st in by_source_out.values())
    total_skus = sum(int(st.get("affected_skus", 0)) for st in by_source_out.values())

    return {
        "ok": True,
        "by_source": by_source_out,
        "total_reserves_shipped": total_reserves_shipped,
        "total_reserved_units": total_reserved_units,
        "total_skus": total_skus,
        "source_errors": source_errors,
        "movement_ids": movement_ids,
        "sync_result": sync_result,
    }
