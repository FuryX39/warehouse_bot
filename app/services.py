from datetime import datetime, timezone
import logging
import time

from app.adapters.base import MarketplaceAdapter, ReservationAction, is_value_configured
from app.adapters.ozon import OzonAdapter
from app.repositories import AVAILABLE_STOCK_SYNC_KEY, InventoryRepository, available_stock_map_hash

logger = logging.getLogger(__name__)

_ADMIN_ALERT_MAX_LEN = 3800
_PER_SOURCE_ALERT_MAX = 3200


def _anchor_key(source: str) -> str:
    return f"{source}_delta_anchor_ts"


def _last_full_key(source: str) -> str:
    return f"{source}_last_full_sync_start_ts"


def _get_anchor(repo: InventoryRepository, source: str) -> int | None:
    v = repo.get_sync_int(_anchor_key(source))
    if v is None and source == "wildberries":
        v = repo.get_sync_int("wb_delta_anchor_ts")
    return v


def _get_last_full(repo: InventoryRepository, source: str) -> int | None:
    v = repo.get_sync_int(_last_full_key(source))
    if v is None and source == "wildberries":
        v = repo.get_sync_int("wb_last_full_sync_start_ts")
    return v


def _set_anchor(repo: InventoryRepository, source: str, ts: int) -> None:
    repo.set_sync_int(_anchor_key(source), ts)


def _set_last_full(repo: InventoryRepository, source: str, ts: int) -> None:
    repo.set_sync_int(_last_full_key(source), ts)


def _reserve_mismatch_report(
    repo: InventoryRepository, source: str, desired: list[ReservationAction]
) -> str | None:
    api_map: dict[str, tuple[str, int]] = {}
    for a in desired:
        if a.source != source:
            continue
        api_map[a.external_order_id] = (a.sku, a.quantity)
    db_rows = repo.get_active_reserve_rows(source)
    db_map = {ext: (sku, qty) for ext, sku, qty in db_rows}
    if api_map == db_map:
        return None
    only_api = sorted(set(api_map.keys()) - set(db_map.keys()))
    only_db = sorted(set(db_map.keys()) - set(api_map.keys()))
    mismatch_qty: list[str] = []
    for k in sorted(set(api_map.keys()) & set(db_map.keys())):
        if api_map[k] != db_map[k]:
            mismatch_qty.append(f"{k}: API={api_map[k]} | БД={db_map[k]}")
    lines = [
        f"{source}: полный снимок API ≠ активные резервы в БД (после reconcile+apply):",
        f"Только в API: {len(only_api)} шт.",
        f"Только в БД: {len(only_db)} шт.",
        f"Разный sku/qty: {len(mismatch_qty)} шт.",
    ]
    if only_api:
        lines.append("Примеры только в API: " + ", ".join(only_api[:20]) + ("…" if len(only_api) > 20 else ""))
    if only_db:
        lines.append("Примеры только в БД: " + ", ".join(only_db[:20]) + ("…" if len(only_db) > 20 else ""))
    if mismatch_qty:
        lines.append("Отличия sku/qty:")
        lines.extend(mismatch_qty[:30])
        if len(mismatch_qty) > 30:
            lines.append(f"… ещё {len(mismatch_qty) - 30}")
    text = "\n".join(lines)
    if len(text) > _PER_SOURCE_ALERT_MAX:
        text = text[: _PER_SOURCE_ALERT_MAX - 20] + "\n…(обрезано)"
    return text


def _adapter_do_full(
    mode_l: str,
    anchor: int | None,
    last_full: int | None,
    interval: int,
    sync_start_ts: int,
) -> bool:
    if mode_l == "full":
        return True
    if mode_l == "delta":
        return anchor is None
    return (
        anchor is None
        or last_full is None
        or (sync_start_ts - last_full >= interval)
    )


def _fetch_adapter_reservations(
    adapter: MarketplaceAdapter, do_full: bool, anchor: int | None, sync_start_ts: int
) -> list[ReservationAction]:
    if do_full:
        return adapter.fetch_reservations_full()
    if anchor is None:
        raise RuntimeError(f"{adapter.name}: дельта без якоря в БД")
    return adapter.fetch_reservations_delta(anchor, sync_start_ts)


class StockCoordinator:
    def __init__(
        self,
        adapters: list[MarketplaceAdapter],
        inventory_repo: InventoryRepository,
        *,
        full_sync_interval_seconds: int = 3600,
    ) -> None:
        self.adapters = adapters
        self.inventory_repo = inventory_repo
        self.full_sync_interval_seconds = full_sync_interval_seconds
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_warnings: list[str] = []

    def sync_cycle(self, mode: str = "auto") -> dict:
        sync_start_ts = int(time.time())
        mode_l = (mode or "auto").strip().lower()
        if mode_l not in ("auto", "delta", "full"):
            mode_l = "auto"

        adapter_do_full: dict[str, bool] = {}
        for adapter in self.adapters:
            if not adapter.is_configured():
                continue
            anchor = _get_anchor(self.inventory_repo, adapter.name)
            last_full = _get_last_full(self.inventory_repo, adapter.name)
            adapter_do_full[adapter.name] = _adapter_do_full(
                mode_l, anchor, last_full, self.full_sync_interval_seconds, sync_start_ts
            )

        adapter_sync_kinds = {k: ("full" if v else "delta") for k, v in adapter_do_full.items()}

        try:
            all_actions: list[ReservationAction] = []
            adapter_errors: list[str] = []
            actions_by_source: dict[str, list[ReservationAction]] = {}
            fetch_ok: dict[str, bool] = {}
            desired_full_by_source: dict[str, list[ReservationAction]] = {}

            for adapter in self.adapters:
                if not adapter.is_configured():
                    continue
                try:
                    do_full = adapter_do_full.get(adapter.name, True)
                    actions = _fetch_adapter_reservations(adapter, do_full, _get_anchor(self.inventory_repo, adapter.name), sync_start_ts)
                    actions_by_source[adapter.name] = actions
                    if do_full or getattr(adapter, "reconcile_on_delta", False):
                        desired_full_by_source[adapter.name] = list(actions)
                    fetch_ok[adapter.name] = True
                    all_actions.extend(actions)
                except Exception as exc:  # noqa: BLE001
                    fetch_ok[adapter.name] = False
                    adapter_errors.append(f"{adapter.name}: fetch failed ({exc})")
                    logger.exception("Adapter fetch failed: %s", adapter.name)

            reconcile_removed = 0
            reconcile_updated = 0
            for adapter in self.adapters:
                if not adapter.is_configured() or not fetch_ok.get(adapter.name):
                    continue
                do_reconcile = adapter_do_full.get(adapter.name, False) or getattr(adapter, "reconcile_on_delta", False)
                if not do_reconcile:
                    continue
                if not getattr(adapter, "supports_reserve_reconciliation", False):
                    continue
                desired = actions_by_source.get(adapter.name, [])
                removed, updated = self.inventory_repo.reconcile_active_reserves(adapter.name, desired)
                reconcile_removed += removed
                reconcile_updated += updated

            order_items_stats = self.inventory_repo.upsert_order_items_from_actions(
                all_actions, sync_start_ts
            )
            inserted = int(order_items_stats.get("inserted", 0))

            mismatch_parts: list[str] = []
            for adapter in self.adapters:
                if not adapter.is_configured() or not fetch_ok.get(adapter.name):
                    continue
                do_reconcile = adapter_do_full.get(adapter.name, False) or getattr(adapter, "reconcile_on_delta", False)
                if not do_reconcile:
                    continue
                if not getattr(adapter, "supports_reserve_reconciliation", False):
                    continue
                desired = desired_full_by_source.get(adapter.name)
                if not desired:
                    continue
                part = _reserve_mismatch_report(self.inventory_repo, adapter.name, desired)
                if part:
                    mismatch_parts.append(part)
                    logger.error("reserve_mismatch %s: %s", adapter.name, part[:500])

            admin_alert: str | None = None
            if mismatch_parts:
                admin_alert = "\n\n──────────\n\n".join(mismatch_parts)
                if len(admin_alert) > _ADMIN_ALERT_MAX_LEN:
                    admin_alert = admin_alert[: _ADMIN_ALERT_MAX_LEN - 30] + "\n…(общий отчёт обрезан)"

            available_stock = self.inventory_repo.get_available_stock_map()
            current_stock_hash = available_stock_map_hash(available_stock)
            last_stock_hash = self.inventory_repo.get_sync_int(AVAILABLE_STOCK_SYNC_KEY)
            stock_changed = last_stock_hash is None or int(last_stock_hash) != int(current_stock_hash)
            reserves_touched = (
                int(order_items_stats.get("touched", 0))
                + int(reconcile_removed)
                + int(reconcile_updated)
            ) > 0
            if reserves_touched:
                stock_changed = True

            if stock_changed:
                stock_push_ok = True
                for adapter in self.adapters:
                    if not adapter.is_configured():
                        continue
                    if isinstance(adapter, OzonAdapter) and not is_value_configured(adapter.warehouse_id):
                        adapter_errors.append(
                            "ozon: задайте OZON_WAREHOUSE_ID (id склада FBS) — пуш остатков пропущен"
                        )
                        stock_push_ok = False
                        continue
                    try:
                        adapter.sync_available_stock(available_stock)
                    except Exception as exc:  # noqa: BLE001
                        adapter_errors.append(f"{adapter.name}: stock sync failed ({exc})")
                        logger.exception("Adapter stock sync failed: %s", adapter.name)
                        stock_push_ok = False
                if stock_push_ok:
                    self.inventory_repo.set_sync_int(AVAILABLE_STOCK_SYNC_KEY, current_stock_hash)
                else:
                    logger.warning(
                        "Stock push hash not updated — повторим пуш на следующем цикле синка"
                    )
            else:
                logger.info("Skip stock push: available stock unchanged")

            for adapter in self.adapters:
                if not adapter.is_configured() or not fetch_ok.get(adapter.name):
                    continue
                _set_anchor(self.inventory_repo, adapter.name, sync_start_ts)
                if adapter_do_full.get(adapter.name, False):
                    _set_last_full(self.inventory_repo, adapter.name, sync_start_ts)

            self.last_run_at = datetime.now(timezone.utc)
            self.last_error = None
            self.last_warnings = adapter_errors
            logger.info(
                "sync_cycle mode=%s kinds=%s sync_start_ts=%s",
                mode_l,
                adapter_sync_kinds,
                sync_start_ts,
            )
            return {
                "ok": True,
                "actions_count": len(all_actions),
                "inserted_reservations": inserted,
                "order_items_upserted": order_items_stats,
                "reconcile_removed": reconcile_removed,
                "reconcile_updated": reconcile_updated,
                "last_run_at": self.last_run_at.isoformat(),
                "adapter_errors": adapter_errors,
                "sync_mode": mode_l,
                "adapter_sync_kinds": adapter_sync_kinds,
                "stock_push_skipped": not stock_changed,
                "admin_alert": admin_alert,
                "sync_start_ts": sync_start_ts,
            }
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.last_warnings = []
            return {"ok": False, "error": self.last_error}
