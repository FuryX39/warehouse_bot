"""WMS: документы с ячейками, locked, перемещения."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogProduct, CatalogRepository
from app.repositories import InventoryRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_bin_transfers_repository import WarehouseBinTransfersRepository
from app.warehouse_inventory_counts_repository import WarehouseInventoryCountsRepository
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.warehouse_transfers_repository import WarehouseTransfersRepository
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository


def _stack(db_url: str):
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    wh_id = int(storage.get_default_warehouse_id())
    main = storage.list_bins(wh_id)[0]
    other = storage.create_bin(wh_id, {"code": "A-01", "name": "Стеллаж A"})

    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    inventory.attach_storage_repo(storage)

    receipts = WarehouseReceiptsRepository(db_url, storage)
    receipts.init_schema()
    writeoffs = WarehouseWriteoffsRepository(db_url, storage)
    writeoffs.init_schema()
    transfers = WarehouseTransfersRepository(db_url, storage)
    transfers.init_schema()
    bin_transfers = WarehouseBinTransfersRepository(db_url, storage)
    bin_transfers.init_schema()
    counts = WarehouseInventoryCountsRepository(db_url, storage, receipts, writeoffs)
    counts.init_schema()

    return {
        "storage": storage,
        "wh_id": wh_id,
        "main": main,
        "other": other,
        "catalog": catalog,
        "receipts": receipts,
        "writeoffs": writeoffs,
        "transfers": transfers,
        "bin_transfers": bin_transfers,
        "counts": counts,
    }


def _product(catalog: CatalogRepository, sku: str) -> int:
    with Session(catalog.engine) as session:
        row = CatalogProduct(
            name=sku,
            sku=sku,
            code=sku[:8],
            created_at_ts=1,
            updated_at_ts=1,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def test_receipt_goes_to_selected_bin_not_only_main(db_url: str) -> None:
    s = _stack(db_url)
    pid = _product(s["catalog"], "SKU-A")
    s["storage"].set_stock(s["wh_id"], "SKU-A", 0, skip_recalc=True, bin_id=s["other"].id)

    s["receipts"].create_receipt(
        {
            "title": "Приход в A-01",
            "warehouse_id": s["wh_id"],
            "bin_id": s["other"].id,
            "items": [{"product_id": pid, "quantity": 4}],
        }
    )
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["other"].id) == 4
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["main"].id) == 0
    assert s["storage"].get_stock(s["wh_id"], "SKU-A") == 4


def test_writeoff_from_bin_strict(db_url: str) -> None:
    s = _stack(db_url)
    pid = _product(s["catalog"], "SKU-A")
    s["storage"].set_stock(s["wh_id"], "SKU-A", 3, skip_recalc=True, bin_id=s["other"].id)

    s["writeoffs"].create_writeoff(
        {
            "title": "Списание из A-01",
            "warehouse_id": s["wh_id"],
            "bin_id": s["other"].id,
            "items": [{"product_id": pid, "quantity": 2}],
        }
    )
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["other"].id) == 1
    assert s["storage"].get_stock(s["wh_id"], "SKU-A") == 1


def test_locked_inventory_writeoff_cannot_edit_or_delete(db_url: str) -> None:
    s = _stack(db_url)
    pid = _product(s["catalog"], "SKU-A")
    s["storage"].set_stock(s["wh_id"], "SKU-A", 5, skip_recalc=True, bin_id=s["main"].id)

    draft = s["counts"].save_draft(
        {
            "warehouse_id": s["wh_id"],
            "bin_id": s["main"].id,
            "comment": "x",
            "items": [{"sku": "SKU-A", "product_id": pid, "name": "SKU-A", "fact_qty": 3}],
        }
    )
    posted = s["counts"].post_count(int(draft.id))
    assert posted.writeoff_id is not None

    wo_id = int(posted.writeoff_id)
    with pytest.raises(ValueError, match="инвентаризац"):
        s["writeoffs"].update_writeoff(
            wo_id,
            {
                "title": "hack",
                "warehouse_id": s["wh_id"],
                "items": [{"product_id": pid, "quantity": 1}],
            },
        )
    with pytest.raises(ValueError, match="инвентаризац"):
        s["writeoffs"].delete_writeoff(wo_id)
    assert s["storage"].get_stock(s["wh_id"], "SKU-A") == 3


def test_bin_transfer_document_moves_between_bins(db_url: str) -> None:
    s = _stack(db_url)
    pid = _product(s["catalog"], "SKU-A")
    s["storage"].set_stock(s["wh_id"], "SKU-A", 10, skip_recalc=True, bin_id=s["main"].id)

    s["bin_transfers"].create_transfer(
        {
            "title": "A→B",
            "warehouse_id": s["wh_id"],
            "from_bin_id": s["main"].id,
            "to_bin_id": s["other"].id,
            "items": [{"product_id": pid, "quantity": 4}],
        }
    )
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["main"].id) == 6
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["other"].id) == 4
    assert s["storage"].get_stock(s["wh_id"], "SKU-A") == 10


def test_inter_warehouse_transfer_uses_bins(db_url: str) -> None:
    s = _stack(db_url)
    pid = _product(s["catalog"], "SKU-A")
    wh2 = s["storage"].create_warehouse({"name": "Склад 2", "code": "WH2"})
    main2 = s["storage"].list_bins(int(wh2.id))[0]
    s["storage"].set_stock(s["wh_id"], "SKU-A", 6, skip_recalc=True, bin_id=s["main"].id)

    s["transfers"].create_transfer(
        {
            "title": "Межсклад",
            "from_warehouse_id": s["wh_id"],
            "to_warehouse_id": int(wh2.id),
            "from_bin_id": s["main"].id,
            "to_bin_id": main2.id,
            "items": [{"product_id": pid, "quantity": 2}],
        }
    )
    assert s["storage"].get_stock(s["wh_id"], "SKU-A", bin_id=s["main"].id) == 4
    assert s["storage"].get_stock(int(wh2.id), "SKU-A", bin_id=main2.id) == 2
