"""НДС, себестоимость и комиссия строк заказа покупателя."""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.warehouse_orders_repository import (
    SOURCE_YM,
    WarehouseOrderRow,
    WarehouseOrdersRepository,
)

logger = logging.getLogger(__name__)

TWOPLACES = Decimal("0.01")
DEFAULT_VAT_RATE = Decimal("22")
ACQUIRING_TARIFF_TYPES = frozenset({"AGENCY_COMMISSION", "PAYMENT_TRANSFER"})


def round_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def parse_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return round_money(value)
    raw = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return round_money(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def money_json(value: Any) -> float | None:
    amount = parse_money(value)
    if amount is None:
        return None
    return float(amount)


def format_money_plain(value: Decimal) -> str:
    text = f"{round_money(value):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def split_vat(gross: Decimal | None, rate: Decimal) -> tuple[Decimal | None, Decimal | None]:
    if gross is None:
        return None, None
    rate = rate if rate is not None else DEFAULT_VAT_RATE
    if rate <= 0:
        return round_money(gross), Decimal("0.00")
    net = round_money(gross / (1 + rate / Decimal("100")))
    vat = round_money(gross - net)
    return net, vat


def comment_is_empty_for_fill(comment: str, posting_id: str) -> bool:
    text = str(comment or "").strip()
    pid = str(posting_id or "").strip()
    return (not text) or (bool(pid) and text == pid)


def item_offer_ids(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("offerId", "shopSku", "sku", "offerID"):
        raw = str(item.get(key) or "").strip()
        if raw and raw not in out:
            out.append(raw)
    return out


def item_buyer_price(item: dict[str, Any]) -> Decimal | None:
    return parse_money(item.get("buyerPrice"))


def item_subsidy(item: dict[str, Any]) -> Decimal | None:
    raw = parse_money(item.get("subsidy"))
    if raw is not None:
        return raw
    total = Decimal("0")
    found = False
    for promo in item.get("promos") or item.get("promotions") or []:
        if not isinstance(promo, dict):
            continue
        amount = parse_money(promo.get("discount") or promo.get("subsidy") or promo.get("amount"))
        if amount is None:
            continue
        total += amount
        found = True
    if found:
        return round_money(total)
    buyer = item_buyer_price(item)
    cabinet = parse_money(item.get("price"))
    if buyer is not None and cabinet is not None and cabinet > buyer:
        return round_money(cabinet - buyer)
    return None


def coinvest_comment_line(sku: str, buyer: Decimal | None, subsidy: Decimal | None) -> str:
    buyer_s = format_money_plain(buyer) if buyer is not None else "0"
    subsidy_s = format_money_plain(subsidy) if subsidy is not None else "0"
    return f"{sku} - цена до соинвеста: {buyer_s}+{subsidy_s}"


def _yandex_adapter(coordinator: Any):
    adapters = getattr(coordinator, "adapters", None) if coordinator is not None else None
    if not isinstance(adapters, (list, tuple)):
        return None
    for adapter in adapters:
        if not callable(getattr(adapter, "fetch_order", None)):
            continue
        configured = getattr(adapter, "is_configured", None)
        if callable(configured) and not configured():
            continue
        return adapter
    return None


def _tariff_amount_by_offer(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    offers = (result or {}).get("offers") or []
    out: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        acquiring = Decimal("0")
        other = Decimal("0")
        found = False
        for tariff in offer.get("tariffs") or []:
            if not isinstance(tariff, dict):
                continue
            amount = parse_money(tariff.get("amount"))
            if amount is None:
                continue
            found = True
            kind = str(tariff.get("type") or "").strip().upper()
            if kind in ACQUIRING_TARIFF_TYPES:
                acquiring += amount
            else:
                other += amount
        if not found:
            out.append({})
            continue
        oid = str(offer.get("offerId") or offer.get("shopSku") or "").strip()
        row: dict[str, Any] = {
            "acquiring": round_money(acquiring),
            "other": round_money(other),
        }
        if oid:
            row["offerId"] = oid
        out.append(row)
    return out


def _match_tariff_row(
    rows: list[dict[str, Any]],
    index: int,
    offer_ids: list[str],
) -> dict[str, Any] | None:
    ids = {x.casefold() for x in offer_ids}
    for row in rows:
        oid = str(row.get("offerId") or "").strip()
        if oid and oid.casefold() in ids:
            return row
    if 0 <= index < len(rows):
        return rows[index]
    return None


def fill_empty_yandex_pricing(
    orders_repo: WarehouseOrdersRepository,
    *,
    coordinator: Any,
    order: WarehouseOrderRow,
) -> WarehouseOrderRow | None:
    if str(order.source) != SOURCE_YM:
        return order
    adapter = _yandex_adapter(coordinator)
    if adapter is None:
        return order
    needs_price = any(getattr(ln, "unit_price", None) is None for ln in order.lines)
    needs_commission = any(getattr(ln, "commission", None) is None for ln in order.lines)
    needs_comment = comment_is_empty_for_fill(order.comment, order.posting_id)
    if not needs_price and not needs_commission and not needs_comment:
        return order
    try:
        payload = adapter.fetch_order(order.posting_id)
    except Exception:
        logger.exception("yandex getOrder failed posting=%s", order.posting_id)
        return order
    if not isinstance(payload, dict):
        return order

    items = [it for it in (payload.get("items") or []) if isinstance(it, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        for oid in item_offer_ids(item):
            by_id.setdefault(oid.casefold(), item)

    line_updates: list[dict[str, Any]] = []
    comment_parts: list[str] = []
    calc_meta: list[dict[str, Any]] = []
    from app.warehouse_order_money import (
        _yandex_category_id,
        _yandex_dimensions,
        _yandex_offer_id,
    )

    snapshots_by_id: dict[str, dict[str, Any]] = {}
    offer_ids = [_yandex_offer_id(item) for item in items]
    offer_ids = [oid for oid in offer_ids if oid]
    fetch_offers = getattr(adapter, "fetch_offer_snapshots", None)
    if needs_commission and callable(fetch_offers) and offer_ids:
        try:
            for snap in fetch_offers(offer_ids) or []:
                if not isinstance(snap, dict):
                    continue
                oid = str(snap.get("offerId") or snap.get("shopSku") or "").strip()
                if not oid and isinstance(snap.get("offer"), dict):
                    oid = str(snap["offer"].get("offerId") or "").strip()
                if oid:
                    snapshots_by_id[oid.casefold()] = snap
        except Exception:
            logger.exception("yandex offer snapshot failed posting=%s", order.posting_id)

    for line in order.lines:
        sku = str(line.sku or "").strip()
        item = by_id.get(sku.casefold())
        if item is None:
            continue
        buyer = item_buyer_price(item)
        subsidy = item_subsidy(item) or Decimal("0.00")
        comment_parts.append(coinvest_comment_line(sku, buyer, subsidy))
        patch: dict[str, Any] = {"sku": sku}
        if getattr(line, "buyer_price", None) is None and buyer is not None:
            patch["buyer_price"] = buyer
        if getattr(line, "subsidy", None) is None:
            patch["subsidy"] = subsidy
        if getattr(line, "unit_price", None) is None and buyer is not None:
            patch["unit_price"] = buyer
        line_updates.append(patch)

        if getattr(line, "commission", None) is not None:
            continue
        oid = _yandex_offer_id(item) or sku
        snap = snapshots_by_id.get(oid.casefold()) or snapshots_by_id.get(sku.casefold()) or {}
        category_id = _yandex_category_id(snap) if snap else None
        if category_id is None:
            continue
        if buyer is None or buyer <= 0:
            continue
        try:
            qty = int(item.get("count") or item.get("quantity") or line.quantity or 1)
        except (TypeError, ValueError):
            qty = int(line.quantity or 1)
        length, width, height, weight = _yandex_dimensions(snap)
        full_price = buyer + subsidy
        calc_meta.append(
            {
                "sku": sku,
                "offer_ids": item_offer_ids(item) or [oid],
                "full": {
                    "offerId": oid,
                    "categoryId": category_id,
                    "price": float(full_price),
                    "length": length,
                    "width": width,
                    "height": height,
                    "weight": weight,
                    "quantity": max(qty, 1),
                },
                "buyer": {
                    "offerId": oid,
                    "categoryId": category_id,
                    "price": float(buyer),
                    "length": length,
                    "width": width,
                    "height": height,
                    "weight": weight,
                    "quantity": max(qty, 1),
                },
            }
        )

    comment = None
    if comment_is_empty_for_fill(order.comment, order.posting_id) and comment_parts:
        comment = "\n".join(comment_parts)

    if calc_meta:
        full_offers = [row["full"] for row in calc_meta]
        buyer_offers = [row["buyer"] for row in calc_meta]
        full_rows: list[dict[str, Decimal]] = []
        buyer_rows: list[dict[str, Decimal]] = []
        try:
            full_rows = _tariff_amount_by_offer(adapter.calculate_tariffs(full_offers))
        except Exception:
            logger.exception("yandex tariffs full base failed posting=%s", order.posting_id)
        try:
            buyer_rows = _tariff_amount_by_offer(adapter.calculate_tariffs(buyer_offers))
        except Exception:
            logger.exception("yandex tariffs buyer base failed posting=%s", order.posting_id)
        by_sku = {str(p.get("sku")): p for p in line_updates}
        for index, meta in enumerate(calc_meta):
            sku = str(meta["sku"])
            line = next((ln for ln in order.lines if ln.sku == sku), None)
            if line is None or getattr(line, "commission", None) is not None:
                continue
            ids = [str(x) for x in meta.get("offer_ids") or []]
            other_row = _match_tariff_row(full_rows, index, ids)
            acq_row = _match_tariff_row(buyer_rows, index, ids)
            if not other_row and not acq_row:
                continue
            other = parse_money((other_row or {}).get("other")) or Decimal("0.00")
            acquiring = parse_money((acq_row or {}).get("acquiring")) or Decimal("0.00")
            patch = by_sku.setdefault(sku, {"sku": sku})
            patch["commission"] = round_money(other + acquiring)

    if not line_updates and comment is None:
        return order
    return orders_repo.apply_empty_line_money(order.id, line_updates, comment=comment)
