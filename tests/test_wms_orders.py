"""MVP WMS: заказы, синк, отгрузка, волны, инвентаризация."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.base import ReservationAction
from app.adapters.ozon import OzonAdapter
from app.catalog_repository import CatalogProduct, CatalogRepository
from app.config import Settings
from app.dealer_analysis_repository import DealerAnalysisRepository
from app.movement_repository import MovementRepository
from app.repositories import InventoryRepository, OrderItem
from app.services import StockCoordinator
from app.stock_sync_runner import run_one_cycle
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_inventory_counts_repository import WarehouseInventoryCountsRepository
from app.warehouse_order_sync import (
    classify_missing_orders,
    group_actions_by_posting,
    upsert_orders_from_actions,
)
from app.warehouse_orders_repository import (
    ORDER_CANCELLED,
    ORDER_IN_WAVE,
    ORDER_OPEN,
    ORDER_PACKED,
    ORDER_SHIPPED,
    ORDERS_LIST_PAGE_SIZE,
    WarehouseOrdersRepository,
)
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.warehouse_shipments_repository import WarehouseShipmentsRepository
from app.warehouse_wave import attach_packing_job_to_orders, release_packing_job_orders
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository
from app.web.server import create_dashboard_app


def _settings(tmp_path, db_url: str) -> Settings:
    return Settings(
        telegram_bot_token="t",
        db_url=db_url,
        movement_db_url=db_url,
        web_dashboard_secret="panel-secret",
        dealer_analysis_db_url=db_url,
        dealer_analysis_data_dir=str(tmp_path / "dealer"),
        warehouse_task_files_data_dir=str(tmp_path / "task_files"),
    )


def _wms_stack(db_url):
    inventory = InventoryRepository(db_url)
    inventory.init_schema()

    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    wh_id = int(storage.get_default_warehouse_id())
    main_bin = storage.list_bins(wh_id)[0]
    inventory.attach_storage_repo(storage)
    inventory.set_sync_source_warehouse_id(wh_id)

    catalog = CatalogRepository(db_url)
    catalog.init_schema()

    from app.crm_repository import CrmRepository

    crm = CrmRepository(db_url)
    crm.init_schema()

    receipts = WarehouseReceiptsRepository(db_url, storage)
    receipts.init_schema()
    writeoffs = WarehouseWriteoffsRepository(db_url, storage)
    writeoffs.init_schema()

    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()

    movement = MovementRepository(db_url)
    movement.init_schema()

    shipments = WarehouseShipmentsRepository(
        db_url, orders, storage, inventory, movement
    )
    shipments.init_schema()

    counts = WarehouseInventoryCountsRepository(db_url, storage, receipts, writeoffs)
    counts.init_schema()

    return {
        "db_url": db_url,
        "storage": storage,
        "wh_id": wh_id,
        "main_bin": main_bin,
        "inventory": inventory,
        "catalog": catalog,
        "orders": orders,
        "shipments": shipments,
        "counts": counts,
        "movement": movement,
    }


def _add_catalog_product(catalog: CatalogRepository, *, sku: str, name: str | None = None) -> int:
    with Session(catalog.engine) as session:
        row = CatalogProduct(
            name=name or sku,
            sku=sku,
            code=sku[:8],
            created_at_ts=1,
            updated_at_ts=1,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def test_two_skus_same_posting_one_order(db_url: str) -> None:
    stack = _wms_stack(db_url)
    actions = [
        ReservationAction(
            source="ozon",
            external_order_id="POST-100:SKU-A",
            sku="SKU-A",
            quantity=2,
        ),
        ReservationAction(
            source="ozon",
            external_order_id="POST-100:SKU-B",
            sku="SKU-B",
            quantity=1,
        ),
    ]
    grouped = group_actions_by_posting(actions)
    assert list(grouped.keys()) == [("ozon", "POST-100")]
    assert len(grouped[("ozon", "POST-100")]) == 2

    upsert_orders_from_actions(
        stack["orders"], warehouse_id=stack["wh_id"], actions=actions
    )
    order = stack["orders"].get_by_posting("ozon", "POST-100")
    assert order is not None
    assert order.number.startswith("ЗК-")
    assert len(order.lines) == 2
    skus = {ln.sku: ln.quantity for ln in order.lines}
    assert skus == {"SKU-A": 2, "SKU-B": 1}
    payload = stack["orders"].to_dict(order)
    assert payload["comment"] == "POST-100"
    assert payload["counterparty_name"] == "Ozon"
    assert payload["created_at_ts"] > 0


def test_list_orders_page_size_fifty(db_url: str) -> None:
    stack = _wms_stack(db_url)
    repo = stack["orders"]
    for i in range(ORDERS_LIST_PAGE_SIZE + 5):
        repo.upsert_from_posting(
            source="ozon",
            posting_id=f"POST-{i:03d}",
            warehouse_id=stack["wh_id"],
            lines=[{"sku": "SKU-A", "quantity": 1, "name": ""}],
        )
    total = repo.count_orders()
    assert total == ORDERS_LIST_PAGE_SIZE + 5
    page1 = repo.list_orders(limit=ORDERS_LIST_PAGE_SIZE, offset=0)
    page2 = repo.list_orders(limit=ORDERS_LIST_PAGE_SIZE, offset=ORDERS_LIST_PAGE_SIZE)
    assert len(page1) == ORDERS_LIST_PAGE_SIZE
    assert len(page2) == 5
    ids1 = {o.id for o in page1}
    ids2 = {o.id for o in page2}
    assert not ids1 & ids2


def test_order_in_reserve_snapshot_not_auto_shipped(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 10, skip_recalc=True)

    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="OZ-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 3, "name": ""}],
    )

    class _Adapter:
        name = "ozon"
        supports_reserve_reconciliation = True
        reconcile_on_delta = False

        def classify_left_reserve(self, posting_id: str) -> str:
            raise AssertionError("classify must not run while posting is in snapshot")

    stats = classify_missing_orders(
        adapter=_Adapter(),
        orders_repo=stack["orders"],
        shipments_repo=stack["shipments"],
        inventory_repo=stack["inventory"],
        source="ozon",
        snapshot_posting_ids={"OZ-1"},
    )
    assert stats == {"cancelled": 0, "shipped": 0, "unknown": 0}
    order = stack["orders"].get_by_posting("ozon", "OZ-1")
    assert order.status == ORDER_OPEN
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 10
    reserve = stack["orders"].reserve_qty_by_sku(stack["wh_id"])
    assert reserve.get("SKU-A") == 3
    assert 10 - reserve.get("SKU-A", 0) == 7


def test_classify_cancel_and_ship(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 5, skip_recalc=True)
    stack["storage"].set_stock(stack["wh_id"], "SKU-B", 5, skip_recalc=True)

    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="CANCEL-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": ""}],
    )
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="SHIP-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 2, "name": ""}],
    )

    class _Adapter:
        def classify_left_reserve(self, posting_id: str) -> str | None:
            if posting_id == "CANCEL-1":
                return "cancel"
            if posting_id == "SHIP-1":
                return "ship"
            return None

    stats = classify_missing_orders(
        adapter=_Adapter(),
        orders_repo=stack["orders"],
        shipments_repo=stack["shipments"],
        inventory_repo=stack["inventory"],
        source="ozon",
        snapshot_posting_ids=set(),
    )
    assert stats["cancelled"] == 1
    assert stats["shipped"] == 1

    cancelled = stack["orders"].get_by_posting("ozon", "CANCEL-1")
    shipped = stack["orders"].get_by_posting("ozon", "SHIP-1")
    assert cancelled.status == ORDER_CANCELLED
    assert shipped.status == ORDER_SHIPPED
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 5
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-B") == 3


def test_fetch_failure_leaves_orders_unchanged(db_url: str) -> None:
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    wh_id = int(storage.get_default_warehouse_id())
    inventory.attach_storage_repo(storage)
    inventory.set_sync_source_warehouse_id(wh_id)

    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()
    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    movement = MovementRepository(db_url)
    movement.init_schema()
    shipments = WarehouseShipmentsRepository(
        db_url, orders, storage, inventory, movement
    )
    shipments.init_schema()

    orders.upsert_from_posting(
        source="ozon",
        posting_id="KEEP-1",
        warehouse_id=wh_id,
        lines=[{"sku": "SKU-A", "quantity": 1, "name": ""}],
    )

    class _FailAdapter:
        name = "ozon"
        warehouse_id = "1"
        supports_reserve_reconciliation = True
        reconcile_on_delta = False

        def is_configured(self) -> bool:
            return True

        def fetch_reservations_full(self) -> list[ReservationAction]:
            raise RuntimeError("marketplace down")

        def fetch_reservations_delta(self, _anchor: int, _ts: int) -> list[ReservationAction]:
            return []

        def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
            pass

        def classify_left_reserve(self, posting_id: str) -> str:
            return "ship"

    coordinator = StockCoordinator(
        adapters=[_FailAdapter()],
        inventory_repo=inventory,
        stock_sync_enabled=True,
        orders_repo=orders,
        shipments_repo=shipments,
    )
    result = run_one_cycle(coordinator, mode="full")
    assert result["ok"] is True
    assert any("fetch failed" in msg for msg in (result.get("adapter_errors") or []))

    order = orders.get_by_posting("ozon", "KEEP-1")
    assert order.status == ORDER_OPEN


def test_main_short_skips_one_order_ships_others(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 5, skip_recalc=True)
    stack["storage"].set_stock(stack["wh_id"], "SKU-B", 0, skip_recalc=True)

    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="OK-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 5, "name": ""}],
    )
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="SHORT-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 1, "name": ""}],
    )

    row = stack["shipments"].ship_postings(
        "ozon",
        ["OK-1", "SHORT-1"],
        title="batch",
        origin="sync",
    )
    assert row is not None
    assert "SHORT-1" in " ".join(row.warnings)

    ok = stack["orders"].get_by_posting("ozon", "OK-1")
    short = stack["orders"].get_by_posting("ozon", "SHORT-1")
    assert ok.status == ORDER_SHIPPED
    assert short.status == ORDER_OPEN
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 0


def test_shipment_idempotent_second_cycle_no_stock_move(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 4, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="IDEM-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 2, "name": ""}],
    )

    stack["shipments"].ship_postings("ozon", ["IDEM-1"], title="first", origin="sync")
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 2

    stack["shipments"].ship_postings("ozon", ["IDEM-1"], title="second", origin="sync")
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 2
    assert stack["orders"].get_by_posting("ozon", "IDEM-1").status == ORDER_SHIPPED


def test_inventory_partial_count_one_delta_unlisted_sku_preserved(db_url: str) -> None:
    stack = _wms_stack(db_url)
    pid_a = _add_catalog_product(stack["catalog"], sku="SKU-A")
    _add_catalog_product(stack["catalog"], sku="SKU-B")
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 10, skip_recalc=True, bin_id=stack["main_bin"].id)
    stack["storage"].set_stock(stack["wh_id"], "SKU-B", 7, skip_recalc=True, bin_id=stack["main_bin"].id)

    draft = stack["counts"].save_draft(
        {
            "warehouse_id": stack["wh_id"],
            "bin_id": stack["main_bin"].id,
            "comment": "partial",
            "items": [{"sku": "SKU-A", "product_id": pid_a, "name": "SKU-A", "fact_qty": 8}],
        }
    )
    posted = stack["counts"].post_count(int(draft.id))
    assert posted.status == "posted"
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A", bin_id=stack["main_bin"].id) == 8
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-B", bin_id=stack["main_bin"].id) == 7

    with pytest.raises(ValueError, match="уже проведён"):
        stack["counts"].post_count(int(draft.id))


def test_wave_cancel_in_wave_to_open_leaves_shipped(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="W-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": ""}],
    )
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="W-2",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 1, "name": ""}],
    )
    o1 = stack["orders"].get_by_posting("ozon", "W-1")
    o2 = stack["orders"].get_by_posting("ozon", "W-2")
    stack["orders"].set_packing_job([o1.id, o2.id], 42)
    stack["orders"].mark_shipped("ozon", "W-2")

    release_packing_job_orders(stack["orders"], 42)

    after1 = stack["orders"].get_by_posting("ozon", "W-1")
    after2 = stack["orders"].get_by_posting("ozon", "W-2")
    assert after1.status == ORDER_OPEN
    assert after1.packing_job_id is None
    assert after2.status == ORDER_SHIPPED


def test_attach_packing_job_from_label_lines(db_url: str) -> None:
    stack = _wms_stack(db_url)
    job = {
        "id": 7,
        "marketplace": "ozon",
        "status": "pending",
        "lines": [
            {"order_id": "LBL-1", "sku": "SKU-A", "product_name": "A"},
            {"order_id": "LBL-1", "sku": "SKU-B", "product_name": "B"},
        ],
    }
    attach_packing_job_to_orders(stack["orders"], job, stack["wh_id"])
    order = stack["orders"].get_by_posting("ozon", "LBL-1")
    assert order.status == ORDER_IN_WAVE
    assert order.packing_job_id == 7
    assert len(order.lines) == 2

    stack["orders"].mark_job_packed(7)
    order = stack["orders"].get_by_posting("ozon", "LBL-1")
    assert order.status == ORDER_PACKED


def test_backfill_from_order_items(db_url: str) -> None:
    stack = _wms_stack(db_url)
    with Session(stack["inventory"].engine) as session:
        session.add(
            OrderItem(
                source="ozon",
                external_order_id="BF-1:SKU-A",
                sku="SKU-A",
                quantity=2,
                state="added",
                first_seen_ts=100,
                last_seen_ts=100,
            )
        )
        session.commit()

    created = stack["orders"].backfill_from_order_items(stack["wh_id"])
    assert created == 1
    order = stack["orders"].get_by_posting("ozon", "BF-1")
    assert order.status == ORDER_OPEN
    assert order.lines[0].quantity == 2


def test_dashboard_startup_skips_order_backfill(db_url: str, tmp_path) -> None:
    settings = _settings(tmp_path, db_url)
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    movement = MovementRepository(db_url)
    movement.init_schema()
    dealer = DealerAnalysisRepository(settings.dealer_analysis_db_url, Path(settings.dealer_analysis_data_dir))
    dealer.init_schema()
    coordinator = StockCoordinator(
        adapters=[],
        inventory_repo=inventory,
        stock_sync_enabled=False,
    )
    with patch.object(WarehouseOrdersRepository, "backfill_from_order_items") as mocked:
        create_dashboard_app(settings, inventory, coordinator, movement, dealer)
        mocked.assert_not_called()


def test_upsert_from_sync_preserves_in_wave_status(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="WAVE-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": ""}],
    )
    order = stack["orders"].get_by_posting("ozon", "WAVE-1")
    stack["orders"].set_packing_job([order.id], 99)

    upsert_orders_from_actions(
        stack["orders"],
        warehouse_id=stack["wh_id"],
        actions=[
            ReservationAction(
                source="ozon",
                external_order_id="WAVE-1:SKU-A",
                sku="SKU-A",
                quantity=2,
            )
        ],
    )
    updated = stack["orders"].get_by_posting("ozon", "WAVE-1")
    assert updated.status == ORDER_IN_WAVE
    assert updated.lines[0].quantity == 2


def test_shipment_draft_does_not_move_stock_until_post(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 10, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="DRAFT-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 3, "name": ""}],
    )
    order = stack["orders"].get_by_posting("ozon", "DRAFT-1")

    draft = stack["shipments"].create_draft([order.id], title="черновик", post=False)
    assert draft.status == "draft"
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 10

    posted = stack["shipments"].post_shipment(int(draft.id))
    assert posted.status == "posted"
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A") == 7
    assert stack["orders"].get_by_posting("ozon", "DRAFT-1").status == ORDER_SHIPPED


def test_sync_full_cycle_ships_when_left_reserve_snapshot(db_url: str) -> None:
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    wh_id = int(storage.get_default_warehouse_id())
    inventory.attach_storage_repo(storage)
    inventory.set_sync_source_warehouse_id(wh_id)
    storage.set_stock(wh_id, "SKU-A", 5, skip_recalc=True)

    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()
    movement = MovementRepository(db_url)
    movement.init_schema()
    shipments = WarehouseShipmentsRepository(db_url, orders, storage, inventory, movement)
    shipments.init_schema()

    orders.upsert_from_posting(
        source="ozon",
        posting_id="GONE-1",
        warehouse_id=wh_id,
        lines=[{"sku": "SKU-A", "quantity": 2, "name": ""}],
    )

    class _EmptySnapshotAdapter:
        name = "ozon"
        warehouse_id = "1"
        supports_reserve_reconciliation = True
        reconcile_on_delta = False

        def is_configured(self) -> bool:
            return True

        def fetch_reservations_full(self) -> list[ReservationAction]:
            return []

        def fetch_reservations_delta(self, _a: int, _b: int) -> list[ReservationAction]:
            return []

        def sync_available_stock(self, _m: dict[str, int]) -> None:
            pass

        def classify_left_reserve(self, posting_id: str) -> str | None:
            return "ship" if posting_id == "GONE-1" else None

    coordinator = StockCoordinator(
        adapters=[_EmptySnapshotAdapter()],
        inventory_repo=inventory,
        stock_sync_enabled=True,
        orders_repo=orders,
        shipments_repo=shipments,
    )
    run_one_cycle(coordinator, mode="full")

    order = orders.get_by_posting("ozon", "GONE-1")
    assert order.status == ORDER_SHIPPED
    assert storage.get_stock(wh_id, "SKU-A") == 3


def test_reconcile_active_reserves_skipped_when_orders_wired(db_url: str, monkeypatch) -> None:
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    wh_id = int(storage.get_default_warehouse_id())
    inventory.attach_storage_repo(storage)
    inventory.set_sync_source_warehouse_id(wh_id)

    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()
    movement = MovementRepository(db_url)
    movement.init_schema()
    shipments = WarehouseShipmentsRepository(db_url, orders, storage, inventory, movement)
    shipments.init_schema()

    called = {"n": 0}
    orig = inventory.reconcile_active_reserves

    def _spy(source, desired):
        called["n"] += 1
        return orig(source, desired)

    monkeypatch.setattr(inventory, "reconcile_active_reserves", _spy)

    class _Adapter:
        name = "ozon"
        warehouse_id = "1"
        supports_reserve_reconciliation = True
        reconcile_on_delta = False

        def is_configured(self) -> bool:
            return True

        def fetch_reservations_full(self) -> list[ReservationAction]:
            return [
                ReservationAction(
                    source="ozon",
                    external_order_id="P-1:SKU-A",
                    sku="SKU-A",
                    quantity=1,
                )
            ]

        def fetch_reservations_delta(self, _a: int, _b: int) -> list[ReservationAction]:
            return []

        def sync_available_stock(self, _m: dict[str, int]) -> None:
            pass

    coordinator = StockCoordinator(
        adapters=[_Adapter()],
        inventory_repo=inventory,
        stock_sync_enabled=True,
        orders_repo=orders,
        shipments_repo=shipments,
    )
    run_one_cycle(coordinator, mode="full")
    assert called["n"] == 0
    assert orders.get_by_posting("ozon", "P-1") is not None


def test_available_stock_uses_warehouse_orders_reserve(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["inventory"].upsert_stock("SKU-A", 20)
    stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="AV-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 6, "name": ""}],
    )
    available = stack["inventory"].get_available_stock_map()
    assert available.get("SKU-A") == 14


def test_ozon_classify_awaiting_deliver_not_ship() -> None:
    adapter = OzonAdapter(client_id="c", api_key="k", warehouse_id="1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": {"status": "awaiting_deliver"}}
    with patch("app.adapters.ozon.requests.post", return_value=mock_resp):
        assert adapter.classify_left_reserve("123") is None


def test_ozon_classify_delivering_is_ship() -> None:
    adapter = OzonAdapter(client_id="c", api_key="k", warehouse_id="1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": {"status": "delivering"}}
    with patch("app.adapters.ozon.requests.post", return_value=mock_resp):
        assert adapter.classify_left_reserve("123") == "ship"


def test_ozon_classify_cancelled_is_cancel() -> None:
    adapter = OzonAdapter(client_id="c", api_key="k", warehouse_id="1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": {"status": "cancelled"}}
    with patch("app.adapters.ozon.requests.post", return_value=mock_resp):
        assert adapter.classify_left_reserve("123") == "cancel"


def test_fbs_ship_confirm_returns_410(db_url: str, tmp_path) -> None:
    settings = _settings(tmp_path, db_url)
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    movement = MovementRepository(db_url)
    movement.init_schema()
    dealer = DealerAnalysisRepository(settings.dealer_analysis_db_url, Path(settings.dealer_analysis_data_dir))
    dealer.init_schema()
    coordinator = StockCoordinator(
        adapters=[],
        inventory_repo=inventory,
        stock_sync_enabled=False,
    )
    app = create_dashboard_app(settings, inventory, coordinator, movement, dealer)
    client = TestClient(app)
    denied = client.post("/api/fbs/ship/confirm", data={"scope": "ozon", "code": "1234"})
    assert denied.status_code == 401

    login = client.post("/api/login", data={"password": "panel-secret"})
    assert login.status_code == 200
    resp = client.post("/api/fbs/ship/confirm", data={"scope": "ozon", "code": "1234"})
    assert resp.status_code == 410
    assert "отключена" in resp.json()["detail"].lower()
