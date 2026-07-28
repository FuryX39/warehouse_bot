"""Очистка остатков на конкретном складе хранения."""

from __future__ import annotations

from app.storage_warehouse_repository import StorageWarehouseRepository


def test_clear_stocks_for_warehouse_only_affects_selected(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'clear_stocks.db').as_posix()}"
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()

    legacy_id = int(storage.get_legacy_warehouse_id())
    main_id = int(storage.get_default_warehouse_id())
    assert legacy_id != main_id

    storage.set_stock(legacy_id, "SKU-A", 10, skip_recalc=True)
    storage.set_stock(legacy_id, "SKU-B", 4, skip_recalc=True)
    storage.set_stock(main_id, "SKU-A", 7, skip_recalc=True)

    affected: list[set[str]] = []
    storage.set_stock_balance_hook(lambda skus: affected.append(set(skus)))

    result = storage.clear_stocks_for_warehouse(legacy_id)
    assert result["cleared_skus"] == 2
    assert result["cleared_units"] == 14
    assert set(result["skus"]) == {"SKU-A", "SKU-B"}
    assert storage.list_stocks_for_warehouse(legacy_id) == {}
    assert storage.get_stock(main_id, "SKU-A") == 7
    assert affected and affected[0] == {"SKU-A", "SKU-B"}

    empty = storage.clear_stocks_for_warehouse(legacy_id)
    assert empty["cleared_skus"] == 0
    assert empty["cleared_units"] == 0
