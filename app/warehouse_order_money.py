"""Сумма заказа с маркетплейса: деньги покупателя, цена кабинета, оценка удержания.

Яндекс: GET /campaigns/{id}/orders/{orderId}
  itemsTotal — платёж покупателя (buyerTotal устарел и может включать баллы Плюса).
  items[].price — цена в кабинете без субсидии продавцу.
  Удержание: POST stats/orders (факт) или POST /v2/tariffs/calculate (оценка).

Wildberries FBS: GET /api/v3/orders
  convertedFinalPrice — к оплате покупателем.
  convertedPrice — кабинет без скидки WB Кошелька.
  Удержание: kgvpMarketplace категории × цена в кабинете (оценка).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "order_money_cache.json"
_YANDEX_ESTIMATE_CM = 10.0
_YANDEX_ESTIMATE_KG = 0.3


def _cache_key(source: str, posting_id: str) -> str:
    return f"{source}:{posting_id}"


def _cache_path() -> Path:
    raw_path = os.getenv("ORDER_MONEY_CACHE", "").strip()
    return Path(raw_path) if raw_path else _CACHE_PATH


def money_from_stored(row: Any) -> dict[str, Any] | None:
    buyer = getattr(row, "buyer_paid", None)
    cabinet = getattr(row, "cabinet_price", None)
    hold = getattr(row, "mp_hold", None)
    if buyer is None and cabinet is None and hold is None:
        return None
    kind = str(getattr(row, "mp_hold_kind", None) or "").strip()
    return {
        "source": str(getattr(row, "source", "") or ""),
        "buyer_paid": buyer,
        "cabinet_price": cabinet,
        "listed_price": getattr(row, "listed_price", None),
        "mp_hold": hold,
        "mp_hold_kind": kind or None,
        "mp_hold_note": str(getattr(row, "mp_hold_note", None) or ""),
        "currency": "RUB",
        "kind": "stored",
    }


def money_from_cache(source: str, posting_id: str) -> dict[str, Any] | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("order money cache unreadable path=%s", path)
        return None
    if not isinstance(data, dict):
        return None
    hit = data.get(_cache_key(source, posting_id))
    return hit if isinstance(hit, dict) else None


def write_money_cache(source: str, posting_id: str, money: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        data[_cache_key(source, posting_id)] = money
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("order money cache write failed path=%s", path, exc_info=True)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wb_kopecks(value: Any) -> float | None:
    raw = _as_float(value)
    if raw is None:
        return None
    return round(raw / 100.0, 2)


def _with_hold(
    money: dict[str, Any],
    hold: float | None,
    *,
    kind: str | None,
    note: str,
) -> dict[str, Any]:
    money["mp_hold"] = round(hold, 2) if hold is not None else None
    money["mp_hold_kind"] = kind
    money["mp_hold_note"] = note
    return money


def money_from_wb_order(order: dict[str, Any]) -> dict[str, Any] | None:
    buyer = _wb_kopecks(order.get("convertedFinalPrice"))
    if buyer is None:
        buyer = _wb_kopecks(order.get("finalPrice"))
    cabinet = _wb_kopecks(order.get("convertedPrice"))
    if cabinet is None:
        cabinet = _wb_kopecks(order.get("price"))
    listed = _wb_kopecks(order.get("salePrice"))
    if buyer is None and cabinet is None and listed is None:
        return None
    return _with_hold(
        {
            "source": "wildberries",
            "buyer_paid": buyer,
            "cabinet_price": cabinet,
            "listed_price": listed,
            "currency": "RUB",
            "kind": "convertedFinalPrice",
        },
        None,
        kind=None,
        note="",
    )


def _yandex_items_sum(order: dict[str, Any], *keys: str) -> float | None:
    total = 0.0
    found = False
    for item in order.get("items") or []:
        if not isinstance(item, dict):
            continue
        raw = None
        for key in keys:
            raw = _as_float(item.get(key))
            if raw is not None:
                break
        if raw is None:
            continue
        try:
            qty = int(item.get("count") or item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        total += raw * max(qty, 1)
        found = True
    return round(total, 2) if found else None


def money_from_yandex_order(order: dict[str, Any]) -> dict[str, Any] | None:
    buyer = _as_float(order.get("itemsTotal"))
    if buyer is None:
        buyer = _yandex_items_sum(order, "buyerPrice")
    if buyer is None:
        buyer = _as_float(order.get("buyerItemsTotal"))
    if buyer is None:
        buyer = _as_float(order.get("buyerTotal"))
    cabinet = _yandex_items_sum(order, "price")
    if cabinet is None:
        cabinet = _as_float(order.get("itemsTotal"))
    listed = _yandex_items_sum(order, "buyerPriceBeforeDiscount", "priceBeforeDiscount")
    if buyer is None and cabinet is None:
        return None
    return _with_hold(
        {
            "source": "yandex_market",
            "buyer_paid": round(buyer, 2) if buyer is not None else None,
            "cabinet_price": round(cabinet, 2) if cabinet is not None else None,
            "listed_price": listed,
            "currency": "RUB",
            "kind": "itemsTotal",
        },
        None,
        kind=None,
        note="",
    )


def hold_from_yandex_stats(stats_order: dict[str, Any] | None) -> float | None:
    if not isinstance(stats_order, dict):
        return None
    amounts: list[float] = []
    has_placement = False
    for row in stats_order.get("commissions") or []:
        if not isinstance(row, dict):
            continue
        amount = _as_float(row.get("actual"))
        if amount is not None:
            amounts.append(amount)
        kind = str(row.get("type") or "").strip().upper()
        if kind in {"FEE", "PLACEMENT"}:
            has_placement = True
    if not amounts or not has_placement:
        return None
    return round(sum(amounts), 2)


def hold_from_yandex_tariffs(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    total = 0.0
    found = False
    for offer in (result or {}).get("offers") or []:
        if not isinstance(offer, dict):
            continue
        for tariff in offer.get("tariffs") or []:
            if not isinstance(tariff, dict):
                continue
            amount = _as_float(tariff.get("amount"))
            if amount is None:
                continue
            total += amount
            found = True
    return round(total, 2) if found else None


def _yandex_offer_id(item: dict[str, Any]) -> str:
    return str(item.get("offerId") or item.get("shopSku") or "").strip()


def _yandex_category_id(snapshot: dict[str, Any]) -> int | None:
    mapping = snapshot.get("mapping") if isinstance(snapshot.get("mapping"), dict) else {}
    for raw in (
        snapshot.get("marketCategoryId"),
        snapshot.get("categoryId"),
        mapping.get("marketCategoryId"),
        mapping.get("categoryId"),
        (snapshot.get("offer") or {}).get("marketCategoryId")
        if isinstance(snapshot.get("offer"), dict)
        else None,
    ):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _yandex_dimensions(snapshot: dict[str, Any]) -> tuple[float, float, float, float]:
    dims = snapshot.get("weightDimensions") if isinstance(snapshot.get("weightDimensions"), dict) else {}
    if not dims and isinstance(snapshot.get("offer"), dict):
        nested = snapshot["offer"].get("weightDimensions")
        if isinstance(nested, dict):
            dims = nested
    length = _as_float(dims.get("length")) or _YANDEX_ESTIMATE_CM
    width = _as_float(dims.get("width")) or _YANDEX_ESTIMATE_CM
    height = _as_float(dims.get("height")) or _YANDEX_ESTIMATE_CM
    weight = _as_float(dims.get("weight")) or _YANDEX_ESTIMATE_KG
    return (
        max(length, 0.1),
        max(width, 0.1),
        max(height, 0.1),
        max(weight, 0.01),
    )


def attach_yandex_hold(adapter: Any, posting_id: str, order: dict[str, Any], money: dict[str, Any]) -> dict[str, Any]:
    stats = None
    fetch_stats = getattr(adapter, "fetch_order_stats", None)
    if callable(fetch_stats):
        try:
            stats = fetch_stats(posting_id)
        except Exception:
            logger.exception("yandex stats hold failed posting=%s", posting_id)
    hold = hold_from_yandex_stats(stats)
    if hold is not None:
        return _with_hold(
            money,
            hold,
            kind="stats",
            note="факт из отчёта stats/orders",
        )
    snapshots_by_id: dict[str, dict[str, Any]] = {}
    fetch_offers = getattr(adapter, "fetch_offer_snapshots", None)
    offer_ids = [_yandex_offer_id(item) for item in order.get("items") or [] if isinstance(item, dict)]
    offer_ids = [oid for oid in offer_ids if oid]
    if callable(fetch_offers) and offer_ids:
        try:
            for snap in fetch_offers(offer_ids) or []:
                oid = str(snap.get("offerId") or snap.get("shopSku") or "").strip()
                if not oid and isinstance(snap.get("offer"), dict):
                    oid = str(snap["offer"].get("offerId") or "").strip()
                if oid:
                    snapshots_by_id[oid] = snap
        except Exception:
            logger.exception("yandex offer snapshot failed posting=%s", posting_id)
    calc_offers: list[dict[str, Any]] = []
    for item in order.get("items") or []:
        if not isinstance(item, dict):
            continue
        oid = _yandex_offer_id(item)
        snap = snapshots_by_id.get(oid) or {}
        category_id = _yandex_category_id(snap)
        if category_id is None:
            continue
        price = _as_float(item.get("price")) or _as_float(item.get("buyerPrice")) or 0.0
        if price <= 0:
            continue
        try:
            qty = int(item.get("count") or item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        length, width, height, weight = _yandex_dimensions(snap)
        calc_offers.append(
            {
                "categoryId": category_id,
                "price": price,
                "length": length,
                "width": width,
                "height": height,
                "weight": weight,
                "quantity": max(qty, 1),
            }
        )
    calculate = getattr(adapter, "calculate_tariffs", None)
    if callable(calculate) and calc_offers:
        try:
            hold = hold_from_yandex_tariffs(calculate(calc_offers))
        except Exception:
            logger.exception("yandex tariffs hold failed posting=%s", posting_id)
            hold = None
        if hold is not None:
            return _with_hold(
                money,
                hold,
                kind="tariffs_estimate",
                note="примерный расчёт калькулятора тарифов",
            )
    return _with_hold(money, None, kind=None, note="удержание пока недоступно")


def attach_wb_hold(adapter: Any, order: dict[str, Any], money: dict[str, Any]) -> dict[str, Any]:
    cabinet = money.get("cabinet_price")
    if cabinet is None:
        cabinet = money.get("buyer_paid")
    try:
        base = float(cabinet) if cabinet is not None else None
    except (TypeError, ValueError):
        base = None
    vendor = str(
        order.get("supplierArticle") or order.get("article") or order.get("vendorCode") or ""
    ).strip()
    try:
        nm_id = int(order.get("nmId") or order.get("nmID") or 0) or None
    except (TypeError, ValueError):
        nm_id = None
    card = None
    fetch_card = getattr(adapter, "fetch_product_card", None)
    if callable(fetch_card):
        try:
            card = fetch_card(vendor_code=vendor, nm_id=nm_id)
        except Exception:
            logger.exception("wb card lookup failed nm=%s sku=%s", nm_id, vendor)
    subject_id = None
    if isinstance(card, dict):
        try:
            subject_id = int(card.get("subjectID") or card.get("subjectId") or 0) or None
        except (TypeError, ValueError):
            subject_id = None
    fetch_tariffs = getattr(adapter, "fetch_commission_tariffs", None)
    rows = []
    if callable(fetch_tariffs):
        try:
            rows = fetch_tariffs() or []
        except Exception:
            logger.exception("wb commission tariffs failed")
            rows = []
    pct = None
    if subject_id:
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                sid = int(row.get("subjectID") or row.get("subjectId") or 0)
            except (TypeError, ValueError):
                continue
            if sid != subject_id:
                continue
            pct = _as_float(row.get("kgvpMarketplace"))
            break
    subject_name = ""
    if isinstance(card, dict):
        subject_name = str(card.get("subjectName") or card.get("object") or "").strip()
    if pct is not None and base is not None and base > 0:
        label = f"оценка КВВ FBS {pct:g}%"
        if subject_name:
            label += f" «{subject_name}»"
        return _with_hold(
            money,
            round(base * pct / 100.0, 2),
            kind="tariffs_estimate",
            note=f"{label} от цены в кабинете",
        )
    return _with_hold(money, None, kind=None, note="удержание пока недоступно")


def fetch_order_money(
    coordinator: Any,
    source: str,
    posting_id: str,
    created_at_ts: int | None = None,
) -> dict[str, Any] | None:
    src = str(source or "").strip()
    pid = str(posting_id or "").strip()
    if not src or not pid:
        return None
    adapters = list(getattr(coordinator, "adapters", None) or [])
    try:
        if src == "wildberries":
            from app.adapters.wildberries import WildberriesAdapter

            adapter = next((a for a in adapters if isinstance(a, WildberriesAdapter) and a.is_configured()), None)
            if adapter is not None and pid.isdigit():
                payload = adapter.fetch_order_by_id(int(pid), around_ts=created_at_ts)
                money = money_from_wb_order(payload) if payload else None
                if money is not None:
                    money = attach_wb_hold(adapter, payload or {}, money)
                    write_money_cache(src, pid, money)
                    return money
        if src == "yandex_market":
            from app.adapters.yandex_market import YandexMarketAdapter

            adapter = next((a for a in adapters if isinstance(a, YandexMarketAdapter) and a.is_configured()), None)
            if adapter is not None:
                payload = adapter.fetch_order(pid)
                money = money_from_yandex_order(payload) if payload else None
                if money is not None:
                    money = attach_yandex_hold(adapter, pid, payload or {}, money)
                    write_money_cache(src, pid, money)
                    return money
    except Exception:
        logger.exception("order money fetch failed source=%s posting=%s", src, pid)
    return money_from_cache(src, pid)
