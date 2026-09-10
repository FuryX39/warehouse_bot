"""Синхронизация складских заказов с снимком резервов МП."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ReservationAction
from app.repositories import InventoryRepository, OrderItem
from app.warehouse_orders_repository import (
    WarehouseOrdersRepository,
    posting_id_from_external,
)
from app.warehouse_shipments_repository import WarehouseShipmentsRepository

logger = logging.getLogger(__name__)


def group_actions_by_posting(
    actions: list[ReservationAction],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for action in actions:
        posting = posting_id_from_external(action.external_order_id)
        if not posting:
            continue
        grouped[(action.source, posting)].append(
            {"sku": action.sku, "quantity": int(action.quantity), "name": ""}
        )
    return grouped


def upsert_orders_from_actions(
    orders_repo: WarehouseOrdersRepository,
    *,
    warehouse_id: int,
    actions: list[ReservationAction],
) -> int:
    grouped = group_actions_by_posting(actions)
    n = 0
    for (source, posting_id), lines in grouped.items():
        orders_repo.upsert_from_posting(
            source=source,
            posting_id=posting_id,
            warehouse_id=int(warehouse_id),
            lines=lines,
        )
        n += 1
    return n


def _mark_order_items_state(
    inventory_repo: InventoryRepository, source: str, posting_id: str, state: str
) -> None:
    prefix = f"{posting_id}:"
    with Session(inventory_repo.engine) as session:
        rows = session.scalars(
            select(OrderItem).where(
                OrderItem.source == source,
                OrderItem.external_order_id.like(prefix + "%"),
            )
        ).all()
        for row in rows:
            if row.state != state:
                row.state = state
        session.commit()


def classify_missing_orders(
    *,
    adapter: Any,
    orders_repo: WarehouseOrdersRepository,
    shipments_repo: WarehouseShipmentsRepository,
    inventory_repo: InventoryRepository,
    source: str,
    snapshot_posting_ids: set[str],
) -> dict[str, int]:
    cancelled = 0
    shipped = 0
    unknown = 0
    to_ship: list[str] = []
    for order in orders_repo.list_active_for_source(source):
        if order.posting_id in snapshot_posting_ids:
            continue
        kind = None
        classify = getattr(adapter, "classify_left_reserve", None)
        if callable(classify):
            try:
                kind = classify(order.posting_id)
            except Exception:
                logger.exception("classify_left_reserve failed source=%s posting=%s", source, order.posting_id)
                kind = None
        if kind == "cancel":
            orders_repo.mark_cancelled(source, order.posting_id)
            _mark_order_items_state(inventory_repo, source, order.posting_id, "cancelled")
            cancelled += 1
        elif kind == "ship":
            to_ship.append(order.posting_id)
        else:
            unknown += 1
            logger.warning(
                "MP status unclear, leave warehouse order source=%s posting=%s",
                source,
                order.posting_id,
            )
    if to_ship:
        row = shipments_repo.ship_postings(
            source,
            to_ship,
            title=f"По статусам МП {source}",
            origin="sync",
        )
        shipped = len(row.posting_ids) if row else 0
    return {"cancelled": cancelled, "shipped": shipped, "unknown": unknown}
