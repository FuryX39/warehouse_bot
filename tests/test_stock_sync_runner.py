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
        self.fetched = False
        self.pushed: dict[str, int] | None = None

    def is_configured(self) -> bool:
        return True

    def fetch_reservations_full(self) -> list[ReservationAction]:
        self.fetched = True
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


class _WbAdapter:
    name = "wildberries"
    warehouse_id = "1"
    supports_reserve_reconciliation = False
    reconcile_on_delta = False

    def __init__(self) -> None:
        self.fetched = False
        self.pushed: dict[str, int] | None = None

    def is_configured(self) -> bool:
        return True

    def fetch_reservations_full(self) -> list[ReservationAction]:
        self.fetched = True
        return [
            ReservationAction(
                source="wildberries",
                external_order_id="wb-1:SKU-1",
                sku="SKU-1",
                quantity=1,
            )
        ]

    def fetch_reservations_delta(self, _anchor: int, _ts: int) -> list[ReservationAction]:
        return []

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        self.pushed = dict(available_stock_by_sku)


def test_disabled_marketplace_skips_orders_and_stock_but_keeps_token(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'sync_mp.db').as_posix()}"
    repo = InventoryRepository(db_url)
    repo.init_schema()
    repo.upsert_stock("SKU-1", 10)
    ozon = _DummyAdapter()
    wb = _WbAdapter()
    repo.set_marketplace_sync_enabled("ozon", False)
    assert ozon.is_configured() is True
    coordinator = StockCoordinator(
        adapters=[ozon, wb],
        inventory_repo=repo,
        full_sync_interval_seconds=3600,
        stock_sync_enabled=True,
    )

    result = run_one_cycle(coordinator, mode="full")

    assert result["ok"] is True
    assert result["marketplace_sync_skipped"] == ["ozon"]
    assert result["adapter_sync_kinds"] == {"wildberries": "full"}
    assert ozon.fetched is False
    assert ozon.pushed is None
    assert wb.fetched is True
    assert wb.pushed is not None
    assert wb.pushed.get("SKU-1") == 9


def test_marketplace_sync_flags_default_on(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'sync_flags.db').as_posix()}"
    repo = InventoryRepository(db_url)
    repo.init_schema()
    assert repo.marketplace_sync_enabled("ozon") is True
    assert repo.marketplace_sync_enabled("wildberries") is True
    assert repo.marketplace_sync_enabled("yandex_market") is True
    repo.set_marketplace_sync_enabled("yandex_market", False)
    assert repo.marketplace_sync_enabled("yandex_market") is False
    assert repo.get_marketplace_sync_flags()["ozon"] is True


def test_all_marketplaces_can_be_disabled(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'sync_all_off.db').as_posix()}"
    repo = InventoryRepository(db_url)
    repo.init_schema()
    repo.upsert_stock("SKU-1", 10)
    ozon = _DummyAdapter()
    wb = _WbAdapter()
    repo.set_marketplace_sync_enabled("ozon", False)
    repo.set_marketplace_sync_enabled("wildberries", False)
    coordinator = StockCoordinator(
        adapters=[ozon, wb],
        inventory_repo=repo,
        stock_sync_enabled=True,
    )

    result = run_one_cycle(coordinator, mode="full")

    assert result["ok"] is True
    assert set(result["marketplace_sync_skipped"]) == {"ozon", "wildberries"}
    assert result["adapter_sync_kinds"] == {}
    assert ozon.fetched is False
    assert wb.fetched is False
    assert ozon.pushed is None
    assert wb.pushed is None
    assert ozon.is_configured() is True
    assert wb.is_configured() is True
