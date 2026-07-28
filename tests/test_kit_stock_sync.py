"""Комплекты: UI-остатки и sync на маркетплейсы."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.catalog_repository import CatalogKitComponent, CatalogProduct, CatalogRepository
from app.repositories import InventoryRepository, OrderItem
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_stock_repository import WarehouseStockRepository


def _setup(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'kits.db').as_posix()}"
    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    inventory = InventoryRepository(db_url)
    inventory.init_schema()
    inventory.attach_storage_repo(storage)
    stock = WarehouseStockRepository(db_url)
    stock.init_schema()
    inventory.set_stock_balance_hook(stock.recalculate_skus)
    return catalog, storage, inventory, stock


def _add_product(session, *, name, sku, code, is_kit=False):
    row = CatalogProduct(
        name=name,
        sku=sku,
        code=code,
        is_kit=is_kit,
        created_at_ts=1,
        updated_at_ts=1,
    )
    session.add(row)
    session.flush()
    return row


def test_kit_available_pushed_and_component_reduced_by_kit_reserve(tmp_path) -> None:
    _catalog, storage, inventory, stock = _setup(tmp_path)
    legacy_id = int(storage.get_legacy_warehouse_id())

    with Session(inventory.engine) as session:
        product = _add_product(session, name="Товар A", sku="SKU-A", code="A1")
        kit = _add_product(session, name="Комплект", sku="KIT-1", code="K1", is_kit=True)
        session.add(
            CatalogKitComponent(
                kit_product_id=int(kit.id),
                component_product_id=int(product.id),
                quantity=2,
            )
        )
        session.commit()

    storage.set_stock(legacy_id, "SKU-A", 10, skip_recalc=True)
    stock.recalculate_skus({"SKU-A", "KIT-1"})

    available = inventory.get_available_stock_map()
    assert available.get("KIT-1") == 5
    assert available.get("SKU-A") == 10

    inventory.upsert_order_items_from_actions(
        [
            __import__("app.adapters.base", fromlist=["ReservationAction"]).ReservationAction(
                source="ozon",
                external_order_id="ord-1:KIT-1",
                sku="KIT-1",
                quantity=2,
            )
        ],
        sync_ts=1,
    )

    rows = {r.sku: r for r in stock.list_by_products({})}
    assert rows["SKU-A"].full_stock == 10
    assert rows["SKU-A"].reserve == 4  # 2 комплекта × 2 шт
    assert rows["SKU-A"].free_stock == 6
    assert rows["KIT-1"].free_stock == 3  # 6 // 2

    available2 = inventory.get_available_stock_map()
    assert available2.get("KIT-1") == 3
    assert available2.get("SKU-A") == 6

    force = inventory.build_force_push_available_map()
    assert force.get("KIT-1") == 3
    assert force.get("SKU-A") == 6

    bd = stock.breakdown("SKU-A", "reserve")
    assert bd["total"] == 4
    assert any(line.get("from_kit") == "KIT-1" for line in bd["lines"])

    snap = {s.sku: s for s in inventory.get_inventory_snapshot()}
    assert snap["SKU-A"].reserve == 4
    assert snap["SKU-A"].available == 6


def test_nested_kit_reserve_allocates_leaf_components(tmp_path) -> None:
    _catalog, storage, inventory, stock = _setup(tmp_path)
    legacy_id = int(storage.get_legacy_warehouse_id())

    with Session(inventory.engine) as session:
        leaf = _add_product(session, name="Leaf", sku="LEAF", code="L1")
        inner = _add_product(session, name="Inner", sku="INNER", code="I1", is_kit=True)
        outer = _add_product(session, name="Outer", sku="OUTER", code="O1", is_kit=True)
        session.add(
            CatalogKitComponent(
                kit_product_id=int(inner.id),
                component_product_id=int(leaf.id),
                quantity=3,
            )
        )
        session.add(
            CatalogKitComponent(
                kit_product_id=int(outer.id),
                component_product_id=int(inner.id),
                quantity=2,
            )
        )
        session.commit()

    storage.set_stock(legacy_id, "LEAF", 30, skip_recalc=True)

    with Session(inventory.engine) as session:
        session.add(
            OrderItem(
                source="wb",
                external_order_id="ord-2",
                sku="OUTER",
                quantity=1,
                state="added",
                first_seen_ts=1,
                last_seen_ts=1,
            )
        )
        session.commit()

    available = inventory.get_available_stock_map()
    # 1 OUTER = 2 INNER = 6 LEAF
    assert available.get("LEAF") == 24
    assert available.get("INNER") == 8
    assert available.get("OUTER") == 4
