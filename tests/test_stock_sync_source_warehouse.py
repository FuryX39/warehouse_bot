"""Тесты склада-источника синхронизации остатков."""

from __future__ import annotations

from app.repositories import STOCK_SYNC_SOURCE_WAREHOUSE_KEY, InventoryRepository
from app.storage_warehouse_repository import StorageWarehouseRepository


def test_sync_source_defaults_to_legacy_then_can_switch_to_main(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'stock_sync.db').as_posix()}"
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    inventory.attach_storage_repo(storage)

    legacy_id = storage.get_legacy_warehouse_id()
    main_id = storage.get_default_warehouse_id()
    assert legacy_id is not None
    assert main_id is not None
    assert inventory.get_sync_source_warehouse_id() == legacy_id
    assert inventory.get_sync_int(STOCK_SYNC_SOURCE_WAREHOUSE_KEY) is None

    storage.set_stock(int(legacy_id), "SKU-L", 7, skip_recalc=True)
    storage.set_stock(int(main_id), "SKU-M", 3, skip_recalc=True)

    available_legacy = inventory.get_available_stock_map()
    assert available_legacy.get("SKU-L") == 7
    assert "SKU-M" not in available_legacy

    inventory.set_sync_source_warehouse_id(int(main_id))
    assert inventory.get_sync_source_warehouse_id() == int(main_id)

    available_main = inventory.get_available_stock_map()
    assert available_main.get("SKU-M") == 3
    assert "SKU-L" not in available_main

    inventory.upsert_stock("SKU-NEW", 5)
    assert storage.get_stock(int(main_id), "SKU-NEW") == 5
    assert storage.get_stock(int(legacy_id), "SKU-NEW") == 0
