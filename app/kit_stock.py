"""Расчёт доступных остатков с учётом комплектов (BOM) и резервов."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogKitComponent, CatalogProduct


def _catalog_ready(session: Session) -> bool:
    try:
        tables = set(inspect(session.get_bind()).get_table_names())
    except Exception:
        return False
    return "catalog_products" in tables and "catalog_kit_components" in tables


def load_kit_leaf_boms(session: Session) -> dict[str, list[tuple[str, int]]]:
    """
    kit_sku -> [(leaf_product_sku, qty), ...] с раскрытием вложенных комплектов.
    """
    if not _catalog_ready(session):
        return {}
    products = {
        int(p.id): p
        for p in session.scalars(select(CatalogProduct)).all()
    }
    if not products:
        return {}
    comps_by_kit: dict[int, list[tuple[int, int]]] = {}
    for row in session.scalars(select(CatalogKitComponent)).all():
        kid = int(row.kit_product_id)
        comps_by_kit.setdefault(kid, []).append(
            (int(row.component_product_id), max(1, int(row.quantity)))
        )

    cache: dict[int, list[tuple[str, int]]] = {}

    def flatten(kit_id: int, visiting: set[int]) -> list[tuple[str, int]]:
        if kit_id in cache:
            return cache[kit_id]
        if kit_id in visiting:
            return []
        visiting.add(kit_id)
        merged: dict[str, int] = {}
        for comp_id, qty in comps_by_kit.get(kit_id, []):
            comp = products.get(comp_id)
            if comp is None:
                continue
            if comp.is_kit:
                for leaf_sku, leaf_qty in flatten(comp_id, visiting):
                    merged[leaf_sku] = merged.get(leaf_sku, 0) + leaf_qty * qty
            else:
                sku = str(comp.sku or "").strip()
                if sku:
                    merged[sku] = merged.get(sku, 0) + qty
        visiting.remove(kit_id)
        out = [(sku, q) for sku, q in sorted(merged.items()) if q > 0]
        cache[kit_id] = out
        return out

    result: dict[str, list[tuple[str, int]]] = {}
    for pid, product in products.items():
        if not product.is_kit:
            continue
        sku = str(product.sku or "").strip()
        if not sku:
            continue
        result[sku] = flatten(pid, set())
    return result


def kit_component_allocations(
    direct_reserves: dict[str, int],
    kit_leaf_boms: dict[str, list[tuple[str, int]]],
) -> dict[str, int]:
    """Сколько единиц листовых товаров зарезервировано заказами комплектов."""
    allocated: dict[str, int] = {}
    for kit_sku, reserve_qty in direct_reserves.items():
        qty_kits = int(reserve_qty)
        if qty_kits <= 0:
            continue
        bom = kit_leaf_boms.get(kit_sku)
        if not bom:
            continue
        for leaf_sku, leaf_qty in bom:
            allocated[leaf_sku] = allocated.get(leaf_sku, 0) + qty_kits * int(leaf_qty)
    return allocated


def compute_kit_aware_available(
    physical: dict[str, int],
    direct_reserves: dict[str, int],
    kit_leaf_boms: dict[str, list[tuple[str, int]]],
    *,
    clamp: bool = False,
) -> dict[str, int]:
    """
    Доступно к продаже:
      - товар: physical - прямой резерв - резерв через комплекты;
      - комплект: min(floor(free_leaf / qty)) после списания резервов комплектов.
    Комплекты без физического остатка всё равно попадают в карту.
    """
    kit_skus = set(kit_leaf_boms.keys())
    allocations = kit_component_allocations(direct_reserves, kit_leaf_boms)

    product_skus: set[str] = set()
    for sku in physical.keys():
        if sku not in kit_skus:
            product_skus.add(sku)
    for sku in direct_reserves.keys():
        if sku not in kit_skus:
            product_skus.add(sku)
    for bom in kit_leaf_boms.values():
        for leaf_sku, _ in bom:
            product_skus.add(leaf_sku)

    product_free: dict[str, int] = {}
    for sku in product_skus:
        free = (
            int(physical.get(sku, 0))
            - int(direct_reserves.get(sku, 0))
            - int(allocations.get(sku, 0))
        )
        product_free[sku] = max(free, 0) if clamp else free

    available: dict[str, int] = dict(product_free)
    for kit_sku, bom in kit_leaf_boms.items():
        if not bom:
            available[kit_sku] = 0
            continue
        candidates: list[int] = []
        for leaf_sku, leaf_qty in bom:
            qty = max(1, int(leaf_qty))
            candidates.append(int(product_free.get(leaf_sku, 0)) // qty)
        kit_free = min(candidates) if candidates else 0
        available[kit_sku] = max(kit_free, 0) if clamp else kit_free

    for sku, reserve in direct_reserves.items():
        if sku in available:
            continue
        free = 0 - int(reserve)
        available[sku] = max(free, 0) if clamp else free

    return available


def expand_skus_with_kit_components(
    session: Session,
    seed: Iterable[str],
) -> set[str]:
    """Добавляет к seed все SKU составляющих комплектов (рекурсивно)."""
    seed_set = {str(s or "").strip() for s in seed if str(s or "").strip()}
    if not seed_set or not _catalog_ready(session):
        return set(seed_set)

    products = {
        str(p.sku or "").strip(): p
        for p in session.scalars(select(CatalogProduct)).all()
        if str(p.sku or "").strip()
    }
    comps_by_kit_id: dict[int, list[int]] = {}
    for row in session.scalars(select(CatalogKitComponent)).all():
        comps_by_kit_id.setdefault(int(row.kit_product_id), []).append(
            int(row.component_product_id)
        )
    sku_by_id = {int(p.id): sku for sku, p in products.items()}

    out = set(seed_set)
    queue = list(seed_set)
    seen = set(seed_set)
    while queue:
        sku = queue.pop()
        product = products.get(sku)
        if product is None or not product.is_kit:
            continue
        for comp_id in comps_by_kit_id.get(int(product.id), []):
            csku = sku_by_id.get(int(comp_id))
            if not csku or csku in seen:
                continue
            seen.add(csku)
            out.add(csku)
            queue.append(csku)
    return out
