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

_PRICE_TOLERANCE_RUB = 1


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


def seller_from_showcase(showcase: float) -> int:
    via_high = showcase / SELLER_TO_SHOWCASE_HIGH
    if via_high >= SELLER_BREAKPOINT:
        return round(via_high)
    return round(showcase / SELLER_TO_SHOWCASE_LOW)


def showcase_from_target_card(target_card: float) -> int:
    target = max(1, round(target_card))
    best_showcase = target
    best_diff = 10**9
    upper = max(target + 1, int(target / 0.71) + 500)
    for showcase in range(max(1, target - 3), upper):
        diff = abs(card_from_showcase(showcase) - target)
        if diff < best_diff:
            best_diff = diff
            best_showcase = showcase
        if diff == 0:
            break
    return best_showcase


def seller_from_target_card(target_card: float) -> int:
    return seller_from_showcase(showcase_from_target_card(target_card))


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


def _needs_reprice(card_price: float, target_price: float) -> bool:
    return abs(card_price - target_price) > _PRICE_TOLERANCE_RUB


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
        note = ""

        if showcase is None or showcase <= 0:
            stats["skipped_no_showcase"] += 1
            note = "нет цены на витрине"
        else:
            stats["with_showcase"] += 1
            estimated_card = card_from_showcase(showcase)
            if catalog_price is None:
                stats["skipped_no_catalog_price"] += 1
                note = "нет цены в выбранном виде цен"
            else:
                stats["with_catalog_price"] += 1
                if _needs_reprice(float(estimated_card), catalog_price):
                    recommended = seller_from_target_card(catalog_price)
                    ws.cell(row=row_offset, column=cols["seller"] + 1, value=int(recommended))
                    ws.cell(row=row_offset, column=cols["min_promo"] + 1, value=int(recommended))
                    updated = True
                    stats["updated"] += 1
                    if estimated_card < catalog_price:
                        note = "цена по карте ниже вида цен"
                    else:
                        note = "цена по карте выше вида цен"
                else:
                    stats["skipped_ok"] += 1
                    note = "цена по карте совпадает"

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
                note=note,
            )
        )

    out = io.BytesIO()
    wb.save(out)
    return RepricerResult(rows=results, stats=stats, workbook_bytes=out.getvalue())
