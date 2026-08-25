"""Сопоставление разобранных КИ с товарами каталога по GTIN."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.catalog_repository import CatalogRepository
from app.marking.cis import CisRecord, parse_cis_list, split_cis_input
from app.marking.gtin import barcode_as_gtin14

MAX_CIS_LINES = 100_000
PREVIEW_SAMPLE = 8


@dataclass(frozen=True)
class ProductRef:
    product_id: int
    sku: str
    name: str


@dataclass
class ProductCodeGroup:
    product: ProductRef
    gtin: str
    codes: list[str] = field(default_factory=list)


@dataclass
class UnmatchedGtin:
    gtin: str
    codes: list[str] = field(default_factory=list)


@dataclass
class GtinConflict:
    gtin: str
    codes: list[str] = field(default_factory=list)
    skus: tuple[str, ...] = ()


@dataclass
class MatchResult:
    groups: list[ProductCodeGroup]
    unmatched: list[UnmatchedGtin]
    conflicts: list[GtinConflict]
    invalid: list[CisRecord]
    duplicate_count: int
    total_lines: int
    unique_codes: int

    @property
    def matched_code_count(self) -> int:
        return sum(len(g.codes) for g in self.groups)


def _product_key(product: ProductRef, gtin: str) -> tuple[int, str]:
    return (product.product_id, gtin)


def build_gtin_index(
    catalog: CatalogRepository,
) -> tuple[dict[str, ProductRef], dict[str, tuple[str, ...]]]:
    """
    GTIN-14 → товар.
    Явный GTIN в карточке важнее совпадения со штрихкодом EAN.
    Если два товара претендуют на один GTIN — конфликт, код не привязываем.
    """
    sources = catalog.load_gtin_match_sources()
    products = {
        int(item["id"]): ProductRef(
            product_id=int(item["id"]),
            sku=str(item["sku"] or ""),
            name=str(item["name"] or ""),
        )
        for item in sources["products"]
    }
    index: dict[str, ProductRef] = {}
    source_kind: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    blocked: set[str] = set()

    def bind(gtin: str, product_id: int, kind: str) -> None:
        product = products.get(int(product_id))
        if product is None or not gtin:
            return
        if gtin in blocked:
            conflicts.setdefault(gtin, set()).add(product.sku)
            return
        existing = index.get(gtin)
        if existing is None:
            index[gtin] = product
            source_kind[gtin] = kind
            return
        if existing.product_id == product.product_id:
            if kind == "gtin":
                source_kind[gtin] = "gtin"
            return
        prev_kind = source_kind.get(gtin, "")
        if prev_kind == "barcode" and kind == "gtin":
            index[gtin] = product
            source_kind[gtin] = "gtin"
            return
        if prev_kind == "gtin" and kind == "barcode":
            return
        blocked.add(gtin)
        bucket = conflicts.setdefault(gtin, set())
        bucket.add(existing.sku)
        bucket.add(product.sku)
        index.pop(gtin, None)

    for product_id, gtin in sources["gtins"]:
        bind(str(gtin), int(product_id), "gtin")
    for product_id, barcode in sources["barcodes"]:
        gtin = barcode_as_gtin14(str(barcode or ""))
        if gtin:
            bind(gtin, int(product_id), "barcode")

    conflict_skus = {gtin: tuple(sorted(skus)) for gtin, skus in conflicts.items()}
    return index, conflict_skus


def match_datamatrix_codes(text: str, catalog: CatalogRepository) -> MatchResult:
    lines = split_cis_input(text)
    if len(lines) > MAX_CIS_LINES:
        raise ValueError(f"Слишком много строк (макс. {MAX_CIS_LINES})")

    index, conflict_skus = build_gtin_index(catalog)
    seen: set[str] = set()
    duplicate_count = 0
    invalid: list[CisRecord] = []
    groups_map: dict[tuple[int, str], ProductCodeGroup] = {}
    unmatched_map: dict[str, UnmatchedGtin] = {}
    conflict_map: dict[str, GtinConflict] = {}

    parsed = parse_cis_list(lines)
    for record in parsed:
        if not record.ok:
            invalid.append(record)
            continue
        key = record.cis or record.raw
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        gtin = record.gtin
        if gtin in conflict_skus:
            item = conflict_map.get(gtin)
            if item is None:
                item = GtinConflict(gtin=gtin, skus=conflict_skus[gtin])
                conflict_map[gtin] = item
            item.codes.append(record.raw)
            continue
        product = index.get(gtin)
        if product is None:
            item = unmatched_map.get(gtin)
            if item is None:
                item = UnmatchedGtin(gtin=gtin)
                unmatched_map[gtin] = item
            item.codes.append(record.raw)
            continue
        group_key = _product_key(product, gtin)
        group = groups_map.get(group_key)
        if group is None:
            group = ProductCodeGroup(product=product, gtin=gtin)
            groups_map[group_key] = group
        group.codes.append(record.raw)

    groups = sorted(groups_map.values(), key=lambda g: (g.product.sku.lower(), g.gtin))
    unmatched = sorted(unmatched_map.values(), key=lambda u: u.gtin)
    conflicts = sorted(conflict_map.values(), key=lambda c: c.gtin)
    return MatchResult(
        groups=groups,
        unmatched=unmatched,
        conflicts=conflicts,
        invalid=invalid,
        duplicate_count=duplicate_count,
        total_lines=len(lines),
        unique_codes=len(seen),
    )


def match_result_preview(result: MatchResult) -> dict:
    def sample(codes: list[str]) -> list[str]:
        return codes[:PREVIEW_SAMPLE]

    return {
        "stats": {
            "total_lines": result.total_lines,
            "unique_codes": result.unique_codes,
            "matched_codes": result.matched_code_count,
            "unmatched_codes": sum(len(u.codes) for u in result.unmatched),
            "conflict_codes": sum(len(c.codes) for c in result.conflicts),
            "invalid_count": len(result.invalid),
            "duplicate_count": result.duplicate_count,
            "product_count": len(result.groups),
        },
        "groups": [
            {
                "product_id": g.product.product_id,
                "sku": g.product.sku,
                "name": g.product.name,
                "gtin": g.gtin,
                "count": len(g.codes),
                "sample": sample(g.codes),
            }
            for g in result.groups
        ],
        "unmatched": [
            {
                "gtin": u.gtin,
                "count": len(u.codes),
                "sample": sample(u.codes),
            }
            for u in result.unmatched
        ],
        "conflicts": [
            {
                "gtin": c.gtin,
                "skus": list(c.skus),
                "count": len(c.codes),
                "sample": sample(c.codes),
            }
            for c in result.conflicts
        ],
        "invalid": [
            {"raw": rec.raw, "error": rec.error}
            for rec in result.invalid[:100]
        ],
    }
