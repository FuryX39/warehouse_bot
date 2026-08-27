"""Цикл резервов и пуша остатков на маркетплейсы. Без Telegram."""

from __future__ import annotations

import logging
import time

from app.services import StockCoordinator

logger = logging.getLogger(__name__)


def run_one_cycle(coordinator: StockCoordinator, *, mode: str = "auto") -> dict:
    result = coordinator.sync_cycle(mode)
    if result.get("ok"):
        logger.info(
            "Sync done. actions=%s inserted=%s removed=%s updated=%s kinds=%s",
            result.get("actions_count"),
            result.get("inserted_reservations"),
            result.get("reconcile_removed", 0),
            result.get("reconcile_updated", 0),
            result.get("adapter_sync_kinds"),
        )
        if result.get("stock_push_disabled"):
            logger.info("Stock push disabled (STOCK_SYNC_ENABLED=0)")
        if result.get("admin_alert"):
            logger.warning("Reserve mismatch after full sync:\n%s", result["admin_alert"])
        errors = result.get("adapter_errors") or []
        if errors:
            logger.warning("Adapter warnings: %s", errors)
    else:
        logger.error("Sync failed: %s", result.get("error"))
    return result


def run_forever(coordinator: StockCoordinator, interval_seconds: int) -> None:
    interval = max(5, int(interval_seconds))
    logger.info("Stock sync loop every %ss (reserves + push)", interval)
    while True:
        try:
            run_one_cycle(coordinator)
        except Exception:
            logger.exception("Sync cycle raised unexpectedly")
        time.sleep(interval)
