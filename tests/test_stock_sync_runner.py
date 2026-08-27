"""Цикл резервов и пуша без Telegram."""

from __future__ import annotations

from app.adapters.base import ReservationAction
from app.repositories import (
    STOCK_SYNC_LAST_FAIL_TS_KEY,
    STOCK_SYNC_LAST_OK_TS_KEY,
    InventoryRepository,
)
from app.services import StockCoordinator
from app.stock_sync_runner import run_one_cycle


class _DummyAdapter:
    name = "ozon"
    warehouse_id = "1"
    supports_reserve_reconciliation = True
    reconcile_on_delta = False

    def __init__(self) -> None:
        self.pushed: dict[str, int] | None = None

    def is_configured(self) -> bool:
        return True

    def fetch_reservations_full(self) -> list[ReservationAction]:
        return [
            ReservationAction(
                source="ozon",
                external_order_id="ord-1:SKU-1",
                sku="SKU-1",
                quantity=2,
            )
        ]

    def fetch_reservations_delta(self, _anchor: int, _ts: int) -> list[ReservationAction]:
        return []

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        self.pushed = dict(available_stock_by_sku)


class _FailFetchAdapter(_DummyAdapter):
    def fetch_reservations_full(self) -> list[ReservationAction]:
        raise RuntimeError("marketplace down")


def test_full_sync_writes_reserve_pushes_stock_and_persists_ok_ts(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'sync.db').as_posix()}"
    repo = InventoryRepository(db_url)
    repo.init_schema()
    repo.upsert_stock("SKU-1", 10)
    adapter = _DummyAdapter()
    coordinator = StockCoordinator(
        adapters=[adapter],
        inventory_repo=repo,
        full_sync_interval_seconds=3600,
        stock_sync_enabled=True,
    )

    result = run_one_cycle(coordinator, mode="full")

    assert result["ok"] is True
    assert result["actions_count"] == 1
    assert int((result.get("order_items_upserted") or {}).get("touched") or 0) >= 1
    assert result["adapter_sync_kinds"] == {"ozon": "full"}
    assert adapter.pushed is not None
    assert adapter.pushed.get("SKU-1") == 8
    assert repo.get_sync_int(STOCK_SYNC_LAST_OK_TS_KEY)
    assert coordinator.last_run_at is not None


def test_fetch_error_is_warning_not_hard_fail(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'sync_fail.db').as_posix()}"
    repo = InventoryRepository(db_url)
    repo.init_schema()
    coordinator = StockCoordinator(
        adapters=[_FailFetchAdapter()],
        inventory_repo=repo,
        stock_sync_enabled=True,
    )
    result = run_one_cycle(coordinator, mode="full")
    assert result["ok"] is True
    assert result["inserted_reservations"] == 0
    assert any("fetch failed" in msg for msg in (result.get("adapter_errors") or []))
    assert repo.get_sync_int(STOCK_SYNC_LAST_OK_TS_KEY)
    assert repo.get_sync_int(STOCK_SYNC_LAST_FAIL_TS_KEY) is None
