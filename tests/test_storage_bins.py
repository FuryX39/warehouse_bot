"""Ячейки склада: MAIN, миграция остатков, перемещение между ячейками."""

from __future__ import annotations

import pytest

from app.storage_warehouse_repository import (
    InsufficientBinStockError,
    StorageWarehouseRepository,
)


def _repo(db_url: str) -> StorageWarehouseRepository:
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    return storage


def test_init_creates_main_bin_and_set_stock_goes_to_main(db_url: str) -> None:
    storage = _repo(db_url)
    wh_id = int(storage.get_default_warehouse_id())
    bins = storage.list_bins(wh_id)
    assert len(bins) == 1
    assert bins[0].code == "MAIN"
    assert bins[0].is_default

    storage.set_stock(wh_id, "SKU-A", 10, skip_recalc=True)
    assert storage.get_stock(wh_id, "SKU-A") == 10
    assert storage.get_stock(wh_id, "SKU-A", bin_id=bins[0].id) == 10
    assert storage.list_stocks_for_warehouse(wh_id) == {"SKU-A": 10}


def test_bin_transfer_keeps_warehouse_total(db_url: str) -> None:
    storage = _repo(db_url)
    wh_id = int(storage.get_default_warehouse_id())
    main = storage.list_bins(wh_id)[0]
    other = storage.create_bin(wh_id, {"code": "A-01", "name": "Стеллаж A"})
    storage.set_stock(wh_id, "SKU-A", 8, skip_recalc=True, bin_id=main.id)
    storage.transfer_between_bins(
        wh_id, "SKU-A", 3, from_bin_id=main.id, to_bin_id=other.id
    )
    assert storage.get_stock(wh_id, "SKU-A", bin_id=main.id) == 5
    assert storage.get_stock(wh_id, "SKU-A", bin_id=other.id) == 3
    assert storage.get_stock(wh_id, "SKU-A") == 8
    assert storage.list_stocks_for_warehouse(wh_id) == {"SKU-A": 8}


def test_strict_adjust_raises_when_bin_empty(db_url: str) -> None:
    storage = _repo(db_url)
    wh_id = int(storage.get_default_warehouse_id())
    main = storage.list_bins(wh_id)[0]
    storage.set_stock(wh_id, "SKU-A", 2, skip_recalc=True)
    with pytest.raises(InsufficientBinStockError):
        storage.adjust_stock(wh_id, "SKU-A", -5, bin_id=main.id, strict=True, skip_recalc=True)
    assert storage.get_stock(wh_id, "SKU-A") == 2


def test_cannot_delete_main_or_bin_with_stock(db_url: str) -> None:
    storage = _repo(db_url)
    wh_id = int(storage.get_default_warehouse_id())
    main = storage.list_bins(wh_id)[0]
    other = storage.create_bin(wh_id, {"code": "B-01", "name": "B"})
    storage.set_stock(wh_id, "SKU-A", 1, skip_recalc=True, bin_id=other.id)
    with pytest.raises(ValueError, match="Основную"):
        storage.delete_bin(wh_id, main.id)
    with pytest.raises(ValueError, match="остатком"):
        storage.delete_bin(wh_id, other.id)
    storage.set_stock(wh_id, "SKU-A", 0, skip_recalc=True, bin_id=other.id)
    storage.delete_bin(wh_id, other.id)
    assert all(b.id != other.id for b in storage.list_bins(wh_id))
