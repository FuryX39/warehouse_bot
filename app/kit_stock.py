"""Расчёт доступных остатков с учётом комплектов (BOM) и резервов."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogKitComponent, CatalogProduct


def _norm(sku: str) -> str:
    return str(sku or "").strip()


def _fold(sku: str) -> str:
    return _norm(sku).casefold()


def _catalog_ready(session: Session) -> bool:
    try:
        tables = set(inspect(session.get_bind()).get_table_names())
    except Exception:
        return False
    return "catalog_products" in tables and "catalog_kit_components" in tables


@dataclass
class KitBomIndex:
    """BOM комплектов + алиасы offer_id (артикул/код, любой регистр) -> sku комплекта."""

    boms: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    # casefold(sku|code) -> канонический kit sku
    offer_to_kit: dict[str, str] = field(default_factory=dict)

    def resolve_kit_sku(self, offer_or_sku: str) -> str | None:
        return self.offer_to_kit.get(_fold(offer_or_sku))

    def bom_for_offer(self, offer_or_sku: str) -> list[tuple[str, int]] | None:
        kit_sku = self.resolve_kit_sku(offer_or_sku)
        if kit_sku is None:
            # точное совпадение на случай отсутствия в индексе
            bom = self.boms.get(_norm(offer_or_sku))
            return bom
        return self.boms.get(kit_sku)


def load_kit_bom_index(session: Session) -> KitBomIndex:
    if not _catalog_ready(session):
        return KitBomIndex()
    products = {
        int(p.id): p
        for p in session.scalars(select(CatalogProduct)).all()
    }
    if not products:
        return KitBomIndex()
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
                sku = _norm(comp.sku)
                if sku:
                    merged[sku] = merged.get(sku, 0) + qty
        visiting.remove(kit_id)
        out = [(sku, q) for sku, q in sorted(merged.items()) if q > 0]
        cache[kit_id] = out
        return out

    index = KitBomIndex()
    for pid, product in products.items():
        if not product.is_kit:
            continue
        sku = _norm(product.sku)
        if not sku:
            continue
        index.boms[sku] = flatten(pid, set())
        index.offer_to_kit[_fold(sku)] = sku
        code = _norm(product.code)
        if code:
            index.offer_to_kit.setdefault(_fold(code), sku)
    return index


def load_kit_leaf_boms(session: Session) -> dict[str, list[tuple[str, int]]]:
    """Обратная совместимость: только BOM по каноническому sku комплекта."""
    return load_kit_bom_index(session).boms


def _canonical_index(*sku_groups: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in sku_groups:
        for raw in group:
            sku = _norm(raw)
            if not sku:
                continue
            fold = _fold(sku)
            if fold not in out:
                out[fold] = sku
    return out


def _remap_qty_map(
    values: dict[str, int],
    canonical_by_fold: dict[str, str],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw, qty in values.items():
        sku = _norm(raw)
        if not sku:
            continue
        canon = canonical_by_fold.get(_fold(sku), sku)
        out[canon] = int(out.get(canon, 0)) + int(qty)
    return out


def kit_component_allocations(
    direct_reserves: dict[str, int],
    kit_leaf_boms: dict[str, list[tuple[str, int]]] | KitBomIndex,
) -> dict[str, int]:
    """Сколько единиц листовых товаров зарезервировано заказами комплектов."""
    index = (
        kit_leaf_boms
        if isinstance(kit_leaf_boms, KitBomIndex)
        else KitBomIndex(
            boms=kit_leaf_boms,
            offer_to_kit={_fold(k): k for k in kit_leaf_boms},
        )
    )
    leaf_canon = _canonical_index(
        index.boms.keys(),
        *(leaf for bom in index.boms.values() for leaf, _ in bom),
        direct_reserves.keys(),
    )
    for leaf_sku, _ in ((leaf, q) for bom in index.boms.values() for leaf, q in bom):
        leaf_canon[_fold(leaf_sku)] = leaf_sku

    allocated: dict[str, int] = {}
    for offer_sku, reserve_qty in direct_reserves.items():
        qty_kits = int(reserve_qty)
        if qty_kits <= 0:
            continue
        bom = index.bom_for_offer(offer_sku)
        if not bom:
            continue
        for leaf_sku, leaf_qty in bom:
            canon_leaf = leaf_canon.get(_fold(leaf_sku), _norm(leaf_sku))
            allocated[canon_leaf] = allocated.get(canon_leaf, 0) + qty_kits * int(leaf_qty)
    return allocated


def compute_kit_aware_available(
    physical: dict[str, int],
    direct_reserves: dict[str, int],
    kit_leaf_boms: dict[str, list[tuple[str, int]]] | KitBomIndex,
    *,
    clamp: bool = False,
) -> dict[str, int]:
    """
    Доступно к продаже:
      - товар: physical - прямой резерв - резерв через комплекты;
      - комплект: min(floor(free_leaf / qty)) после списания резервов комплектов.
    Артикулы сопоставляются без учёта регистра; заказ комплекта можно найти по sku или code.
    """
    index = (
        kit_leaf_boms
        if isinstance(kit_leaf_boms, KitBomIndex)
        else KitBomIndex(
            boms=kit_leaf_boms,
            offer_to_kit={_fold(k): k for k in kit_leaf_boms},
        )
    )
    leaf_skus = [leaf for bom in index.boms.values() for leaf, _ in bom]
    canonical_by_fold = _canonical_index(
        index.boms.keys(),
        leaf_skus,
        physical.keys(),
        direct_reserves.keys(),
    )
    for kit_sku in index.boms:
        canonical_by_fold[_fold(kit_sku)] = kit_sku
    for leaf_sku in leaf_skus:
        canonical_by_fold[_fold(leaf_sku)] = leaf_sku
    # Заказы комплекта по коду/другому регистру склеиваем на канонический kit sku.
    for offer_fold, kit_sku in index.offer_to_kit.items():
        canonical_by_fold[offer_fold] = kit_sku

    physical_n = _remap_qty_map(physical, canonical_by_fold)
    reserves_n = _remap_qty_map(direct_reserves, canonical_by_fold)
    kit_skus = set(index.boms.keys())
    allocations = kit_component_allocations(reserves_n, index)

    product_skus: set[str] = set()
    for sku in physical_n.keys():
        if sku not in kit_skus:
            product_skus.add(sku)
    for sku in reserves_n.keys():
        if sku not in kit_skus:
            product_skus.add(sku)
    for leaf_sku in leaf_skus:
        product_skus.add(leaf_sku)

    product_free: dict[str, int] = {}
    for sku in product_skus:
        free = (
            int(physical_n.get(sku, 0))
            - int(reserves_n.get(sku, 0))
            - int(allocations.get(sku, 0))
        )
        product_free[sku] = max(free, 0) if clamp else free

    available: dict[str, int] = dict(product_free)
    for kit_sku, bom in index.boms.items():
        if not bom:
            available[kit_sku] = 0
            continue
        candidates = [
            int(product_free.get(leaf_sku, 0)) // max(1, int(leaf_qty))
            for leaf_sku, leaf_qty in bom
        ]
        kit_free = min(candidates) if candidates else 0
        available[kit_sku] = max(kit_free, 0) if clamp else kit_free

    for sku, reserve in reserves_n.items():
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
    seed_set = {_norm(s) for s in seed if _norm(s)}
    if not seed_set or not _catalog_ready(session):
        return set(seed_set)

    products_by_fold: dict[str, CatalogProduct] = {}
    sku_by_id: dict[int, str] = {}
    for p in session.scalars(select(CatalogProduct)).all():
        sku = _norm(p.sku)
        if not sku:
            continue
        products_by_fold[_fold(sku)] = p
        sku_by_id[int(p.id)] = sku
        code = _norm(p.code)
        if code:
            products_by_fold.setdefault(_fold(code), p)

    comps_by_kit_id: dict[int, list[int]] = {}
    for row in session.scalars(select(CatalogKitComponent)).all():
        comps_by_kit_id.setdefault(int(row.kit_product_id), []).append(
            int(row.component_product_id)
        )

    out = set(seed_set)
    queue = list(seed_set)
    seen_fold = {_fold(s) for s in seed_set}
    while queue:
        sku = queue.pop()
        product = products_by_fold.get(_fold(sku))
        if product is None or not product.is_kit:
            continue
        for comp_id in comps_by_kit_id.get(int(product.id), []):
            csku = sku_by_id.get(int(comp_id))
            if not csku:
                continue
            cf = _fold(csku)
            if cf in seen_fold:
                continue
            seen_fold.add(cf)
            out.add(csku)
            queue.append(csku)
    return out
