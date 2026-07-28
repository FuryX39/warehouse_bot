"""Регистр/код артикула комплекта не должен ломать резерв составляющих."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.base import ReservationAction
from app.catalog_repository import CatalogKitComponent, CatalogProduct, CatalogRepository
from app.kit_stock import compute_kit_aware_available, kit_component_allocations, load_kit_bom_index
from app.repositories import InventoryRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_stock_repository import WarehouseStockRepository


def _boot(tmp_path, name: str):
    db_url = f"sqlite:///{(tmp_path / name).as_posix()}"
    CatalogRepository(db_url).init_schema()
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    inventory.attach_storage_repo(storage)
    stock = WarehouseStockRepository(db_url)
    stock.init_schema()
    inventory.set_stock_balance_hook(stock.recalculate_skus)
    return storage, inventory, stock


def test_kit_reserve_matches_case_insensitive_sku(tmp_path) -> None:
    storage, inventory, stock = _boot(tmp_path, "kit_case.db")

    with Session(inventory.engine) as session:
        product = CatalogProduct(
            name="Part", sku="part-a", code="P1", is_kit=False, created_at_ts=1, updated_at_ts=1
        )
        kit = CatalogProduct(
            name="Kit", sku="KIT-CASE", code="K1", is_kit=True, created_at_ts=1, updated_at_ts=1
        )
        session.add_all([product, kit])
        session.flush()
        session.add(
            CatalogKitComponent(
                kit_product_id=int(kit.id),
                component_product_id=int(product.id),
                quantity=2,
            )
        )
        session.commit()

    legacy_id = int(storage.get_legacy_warehouse_id())
    storage.set_stock(legacy_id, "PART-A", 10, skip_recalc=True)

    inventory.upsert_order_items_from_actions(
        [
            ReservationAction(
                source="ozon",
                external_order_id="ord:kit",
                sku="kit-case",
                quantity=2,
            )
        ],
        sync_ts=1,
    )

    available = inventory.get_available_stock_map()
    assert available.get("KIT-CASE") == 3
    assert available.get("part-a", available.get("PART-A")) == 6

    rows = {r.sku.casefold(): r for r in stock.list_by_products({})}
    assert rows["part-a"].reserve == 4
    assert rows["part-a"].free_stock == 6


def test_kit_reserve_matches_by_product_code(tmp_path) -> None:
    storage, inventory, stock = _boot(tmp_path, "kit_code.db")

    with Session(inventory.engine) as session:
        product = CatalogProduct(
            name="Part", sku="LEAF-X", code="LX", is_kit=False, created_at_ts=1, updated_at_ts=1
        )
        kit = CatalogProduct(
            name="Kit", sku="KIT-SKU", code="KIT-OFFER", is_kit=True, created_at_ts=1, updated_at_ts=1
        )
        session.add_all([product, kit])
        session.flush()
        session.add(
            CatalogKitComponent(
                kit_product_id=int(kit.id),
                component_product_id=int(product.id),
                quantity=2,
            )
        )
        session.commit()

    legacy_id = int(storage.get_legacy_warehouse_id())
    storage.set_stock(legacy_id, "LEAF-X", 10, skip_recalc=True)

    # На МП offer_id = код комплекта, не артикул.
    inventory.upsert_order_items_from_actions(
        [
            ReservationAction(
                source="ozon",
                external_order_id="ord:code",
                sku="KIT-OFFER",
                quantity=1,
            )
        ],
        sync_ts=1,
    )

    available = inventory.get_available_stock_map()
    assert available.get("KIT-SKU") == 4
    assert available.get("LEAF-X") == 8
    rows = {r.sku: r for r in stock.list_by_products({})}
    assert rows["LEAF-X"].reserve == 2


def test_allocation_lookup_by_folded_kit_sku(tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'kit_fold.db').as_posix()}"
    CatalogRepository(db_url).init_schema()
    with Session(CatalogRepository(db_url).engine) as session:
        product = CatalogProduct(
            name="P", sku="LEAF1", code="L1", is_kit=False, created_at_ts=1, updated_at_ts=1
        )
        kit = CatalogProduct(
            name="K", sku="MyKit", code="K9", is_kit=True, created_at_ts=1, updated_at_ts=1
        )
        session.add_all([product, kit])
        session.flush()
        session.add(
            CatalogKitComponent(
                kit_product_id=int(kit.id),
                component_product_id=int(product.id),
                quantity=3,
            )
        )
        session.commit()

    with Session(CatalogRepository(db_url).engine) as session:
        index = load_kit_bom_index(session)
    alloc = kit_component_allocations({"mykit": 2}, index)
    assert alloc.get("LEAF1") == 6

    available = compute_kit_aware_available(
        {"leaf1": 20},
        {"MYKIT": 2},
        index,
        clamp=True,
    )
    assert available.get("MyKit") == 4
    assert available.get("LEAF1") == 14
