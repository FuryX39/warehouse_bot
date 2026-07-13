"""Репрайсер Яндекс Маркет: расчёт цены по карте и рекомендации по виду цен."""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

SELLER_BREAKPOINT = 500
SELLER_TO_SHOWCASE_LOW = 0.8467
SELLER_TO_SHOWCASE_HIGH = 0.6084

# showcase (руб) -> коэффициент «цена по карте / витрина»
_CARD_TIERS: tuple[tuple[float, float, float], ...] = (
    (0, 280, 0.7195),
    (280, 500, 0.7694),
    (500, 750, 0.8051),
    (750, 1000, 0.8892),
    (1000, 1500, 0.9597),
    (1500, 10_000_000, 0.9700),
)

_CARD_HIGHER_THRESHOLD = 1.30  # меняем, если цена по карте выше вида цен на 30%+
_CARD_LOWER_THRESHOLD = 0.90   # меняем, если цена по карте ниже вида цен на 10%+


@dataclass(frozen=True)
class RepricerRowResult:
    row_index: int
    sku: str
    name: str
    seller_price: Optional[float]
    showcase_price: Optional[float]
    estimated_card_price: Optional[int]
    catalog_price: Optional[float]
    recommended_seller_price: Optional[int]
    updated: bool
    missing_catalog_price: bool
    note: str


@dataclass(frozen=True)
class RepricerResult:
    rows: list[RepricerRowResult]
    stats: dict[str, int]
    workbook_bytes: bytes


def _card_multiplier(showcase: float) -> float:
    for lo, hi, mult in _CARD_TIERS:
        if lo <= showcase < hi:
            return mult
    return 0.97


def showcase_from_seller(seller: float) -> int:
    if seller < SELLER_BREAKPOINT:
        return round(seller * SELLER_TO_SHOWCASE_LOW)
    return round(seller * SELLER_TO_SHOWCASE_HIGH)


def card_from_showcase(showcase: float) -> int:
    return round(showcase * _card_multiplier(showcase))


def card_from_seller(seller: int) -> int:
    return card_from_showcase(showcase_from_seller(seller))


def _seller_search_upper(target_card: int) -> int:
    return max(target_card * 3, int(target_card / (SELLER_TO_SHOWCASE_HIGH * 0.7195)) + 500)


def seller_from_showcase(showcase: float) -> int:
    candidates: list[int] = []
    via_low = round(showcase / SELLER_TO_SHOWCASE_LOW)
    if via_low < SELLER_BREAKPOINT and showcase_from_seller(via_low) == round(showcase):
        candidates.append(via_low)
    via_high = round(showcase / SELLER_TO_SHOWCASE_HIGH)
    if via_high >= SELLER_BREAKPOINT and showcase_from_seller(via_high) == round(showcase):
        candidates.append(via_high)
    if not candidates:
        via_high_f = showcase / SELLER_TO_SHOWCASE_HIGH
        if via_high_f >= SELLER_BREAKPOINT:
            candidates.append(round(via_high_f))
        else:
            candidates.append(round(showcase / SELLER_TO_SHOWCASE_LOW))
    return min(candidates)


def showcase_from_target_card(target_card: float, *, at_least: bool = False) -> int:
    target = max(1, round(target_card))
    upper = max(target + 1, int(target / 0.71) + 500)
    best_showcase = target
    best_diff = 10**9
    for showcase in range(max(1, target - 3), upper):
        card = card_from_showcase(showcase)
        if at_least:
            if card >= target:
                return showcase
            continue
        diff = abs(card - target)
        if diff < best_diff:
            best_diff = diff
            best_showcase = showcase
        if diff == 0:
            break
    return best_showcase


def seller_from_target_card(
    target_card: float,
    *,
    current_seller: Optional[int] = None,
    raise_price: bool = True,
) -> int:
    """Подбор цены продавца с учётом соинвеста и скидки по карте.

    При поднятии (raise_price=True) — минимальная цена продавца, при которой
    цена по карте не ниже цели. При снижении — максимальная, при которой не выше.
    """
    target = max(1, round(target_card))
    if raise_price:
        start = max(1, int(current_seller or 1))
        upper = _seller_search_upper(target)
        for seller in range(start, upper + 1):
            if card_from_seller(seller) >= target:
                return seller
        return upper

    start = int(current_seller) if current_seller is not None else _seller_search_upper(target)
    start = max(1, start)
    for seller in range(start, 0, -1):
        if card_from_seller(seller) <= target:
            return seller
    return 1


def _parse_number(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _find_header_row(rows: list[tuple]) -> int:
    for idx, row in enumerate(rows[:25]):
        for cell in row:
            text = str(cell or "").casefold()
            if "sku" in text and "ваш" in text:
                return idx
            if text in {"ваш sku *", "ваш sku"}:
                return idx
    raise ValueError("Не найдена строка заголовков (колонка «Ваш SKU»)")


def _column_map(header: tuple) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        text = str(cell or "").strip().casefold()
        if not text:
            continue
        if "sku" in text:
            mapping["sku"] = idx
        elif text == "цена *" or text.startswith("цена *"):
            mapping["seller"] = idx
        elif "минимум" in text and "акци" in text:
            mapping["min_promo"] = idx
        elif "витрин" in text:
            mapping["showcase"] = idx
        elif text == "название товара" or ("название" in text and "товар" in text):
            mapping["name"] = idx
    missing = [key for key in ("sku", "seller", "showcase") if key not in mapping]
    if missing:
        raise ValueError(f"В файле нет обязательных колонок: {', '.join(missing)}")
    if "min_promo" not in mapping and "seller" in mapping:
        mapping["min_promo"] = mapping["seller"] + 1
    if "name" not in mapping:
        mapping["name"] = mapping["sku"] + 1
    return mapping


def _needs_reprice(card_price: float, catalog_price: float) -> bool:
    if catalog_price <= 0:
        return False
    if card_price >= catalog_price * _CARD_HIGHER_THRESHOLD:
        return True
    if card_price <= catalog_price * _CARD_LOWER_THRESHOLD:
        return True
    return False


def _reprice_note(card_price: float, catalog_price: float, *, updated: bool) -> str:
    if updated:
        if card_price < catalog_price:
            return "цена по карте ниже вида цен на 10% и более"
        return "цена по карте выше вида цен на 30% и более"
    if card_price < catalog_price:
        return "цена по карте ниже, но менее чем на 10%"
    if card_price > catalog_price:
        return "цена по карте выше, но менее чем на 30%"
    return "цена по карте совпадает с видом цен"


def _catalog_price_map(
    catalog_repo: Any,
    price_type_id: int,
) -> dict[str, float]:
    products = catalog_repo.list_products_for_price_type(int(price_type_id), {})
    out: dict[str, float] = {}
    for item in products:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        parsed = _parse_number(item.get("price"))
        if parsed is None:
            continue
        out[sku.casefold()] = parsed
    return out


def process_yandex_prices_workbook(
    data: bytes,
    *,
    catalog_repo: Any,
    price_type_id: int,
    price_type_name: str = "",
) -> RepricerResult:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Не установлен openpyxl") from exc

    if not data:
        raise ValueError("Файл пустой")

    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    if ws is None:
        raise ValueError("В файле нет листа с данными")

    rows = [tuple(row) for row in ws.iter_rows(values_only=True)]
    header_idx = _find_header_row(rows)
    cols = _column_map(rows[header_idx])
    prices_by_sku = _catalog_price_map(catalog_repo, price_type_id)
    missing_price_label = (
        f"У товара нет вида цен «{price_type_name.strip()}»"
        if price_type_name and price_type_name.strip()
        else "У товара нет выбранного вида цен"
    )

    results: list[RepricerRowResult] = []
    stats = {
        "total_rows": 0,
        "with_showcase": 0,
        "with_catalog_price": 0,
        "updated": 0,
        "skipped_no_showcase": 0,
        "skipped_no_catalog_price": 0,
        "skipped_ok": 0,
    }

    for row_offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        sku_raw = row[cols["sku"]] if cols["sku"] < len(row) else None
        sku = str(sku_raw or "").strip()
        if not sku:
            continue
        stats["total_rows"] += 1

        name_cell = row[cols["name"]] if cols["name"] < len(row) else ""
        name = str(name_cell or "").strip()
        seller = _parse_number(row[cols["seller"]] if cols["seller"] < len(row) else None)
        showcase = _parse_number(row[cols["showcase"]] if cols["showcase"] < len(row) else None)
        catalog_price = prices_by_sku.get(sku.casefold())

        estimated_card: Optional[int] = None
        recommended: Optional[int] = None
        updated = False
        missing_catalog_price = False
        note = ""

        if showcase is None or showcase <= 0:
            stats["skipped_no_showcase"] += 1
            note = "нет цены на витрине"
        else:
            stats["with_showcase"] += 1
            estimated_card = card_from_showcase(showcase)
            if catalog_price is None:
                stats["skipped_no_catalog_price"] += 1
                missing_catalog_price = True
                note = missing_price_label
            else:
                stats["with_catalog_price"] += 1
                if _needs_reprice(float(estimated_card), catalog_price):
                    raise_price = float(estimated_card) < catalog_price
                    current_seller = int(round(seller)) if seller and seller > 0 else None
                    recommended = seller_from_target_card(
                        catalog_price,
                        current_seller=current_seller,
                        raise_price=raise_price,
                    )
                    ws.cell(row=row_offset, column=cols["seller"] + 1, value=int(recommended))
                    ws.cell(row=row_offset, column=cols["min_promo"] + 1, value=int(recommended))
                    updated = True
                    stats["updated"] += 1
                    note = _reprice_note(float(estimated_card), catalog_price, updated=True)
                else:
                    stats["skipped_ok"] += 1
                    note = _reprice_note(float(estimated_card), catalog_price, updated=False)

        results.append(
            RepricerRowResult(
                row_index=row_offset,
                sku=sku,
                name=name,
                seller_price=seller,
                showcase_price=showcase,
                estimated_card_price=estimated_card,
                catalog_price=catalog_price,
                recommended_seller_price=recommended,
                updated=updated,
                missing_catalog_price=missing_catalog_price,
                note=note,
            )
        )

    out = io.BytesIO()
    wb.save(out)
    return RepricerResult(rows=results, stats=stats, workbook_bytes=out.getvalue())
