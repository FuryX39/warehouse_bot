"""Отчёт «Потребность Яндекса»: остатки FBY по всем кластерам плюс заказы за 30/60/90 дней."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

from app.adapters.base import is_value_configured
from app.catalog_repository import CatalogRepository
from app.product_demand_report import (
    ProductDemandResult,
    _catalog_sku_map,
    attach_demand_columns,
    build_demand_workbook,
    expand_demand_stock_rows,
    sales_window_30_days,
)
from app.stock_by_warehouse_report import MISSING_NAME, StockByWarehouseRow
from app.yandex_fbo_api import YandexFboApi, supply_request_is_in_transit

logger = logging.getLogger(__name__)

SOURCE_ID = "yandex"
SOURCE_TITLE = "Склады Яндекса"
WAREHOUSE_TITLE = "Все кластеры FBY"
FILENAME = "potrebnost_tovarov_yandex.xlsx"

_SKIP_SALES_STATUSES = frozenset(
    {
        "CANCELLED_BEFORE_PROCESSING",
        "CANCELLED_IN_DELIVERY",
        "CANCELLED_IN_PROCESSING",
        "UNPAID",
        "UNKNOWN",
    }
)
_CANCELLED_STATUS_PREFIX = "CANCELLED"

FetchWarehousesFn = Callable[[], list[dict[str, Any]]]
FetchInTransitFn = Callable[[], dict[str, int]]
FetchSalesFn = Callable[[date, date], dict[str, int]]


def _qty(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def _add_qty(by_key: dict[str, tuple[str, int]], sku: str, qty: int) -> None:
    if qty <= 0:
        return
    key = sku.casefold()
    prev_sku, prev_qty = by_key.get(key, (sku, 0))
    by_key[key] = (prev_sku, prev_qty + qty)


def merge_sku_quantities(*maps: dict[str, int]) -> dict[str, int]:
    by_key: dict[str, tuple[str, int]] = {}
    for mapping in maps:
        for sku, qty in (mapping or {}).items():
            text = str(sku or "").strip()
            if not text:
                continue
            _add_qty(by_key, text, _qty(qty))
    return {sku: qty for sku, qty in by_key.values()}


def aggregate_fby_stocks(
    warehouses: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Сумма AVAILABLE и QUARANTINE по всем складам (кластерам) на shopSku."""
    available: dict[str, tuple[str, int]] = {}
    quarantine: dict[str, tuple[str, int]] = {}
    for warehouse in warehouses or []:
        data = _as_dict(warehouse)
        for offer in _as_list(data.get("offers") or data.get("skus")):
            item = _as_dict(offer)
            sku = str(
                item.get("offerId")
                or item.get("shopSku")
                or item.get("sku")
                or ""
            ).strip()
            if not sku:
                continue
            stocks = _as_list(item.get("stocks") or item.get("stock"))
            avail_qty = 0
            quarantine_qty = 0
            for stock in stocks:
                row = _as_dict(stock)
                kind = str(row.get("type") or "").strip().upper()
                count = _qty(row.get("count") or row.get("quantity"))
                if kind == "AVAILABLE":
                    avail_qty += count
                elif kind == "QUARANTINE":
                    quarantine_qty += count
            _add_qty(available, sku, avail_qty)
            _add_qty(quarantine, sku, quarantine_qty)
    return (
        {sku: qty for sku, qty in available.values()},
        {sku: qty for sku, qty in quarantine.values()},
    )


def aggregate_fby_sales(orders: list[dict[str, Any]]) -> dict[str, int]:
    by_key: dict[str, tuple[str, int]] = {}
    for raw in orders or []:
        order = _as_dict(raw)
        status = str(order.get("status") or "").strip().upper()
        if status in _SKIP_SALES_STATUSES or status.startswith(_CANCELLED_STATUS_PREFIX):
            continue
        for item in _as_list(order.get("items")):
            row = _as_dict(item)
            sku = str(row.get("shopSku") or row.get("offerId") or row.get("sku") or "").strip()
            if not sku:
                continue
            qty = _qty(row.get("count") if row.get("count") is not None else row.get("quantity"))
            rejected = 0
            for detail in _as_list(row.get("details")):
                blob = _as_dict(detail)
                if str(blob.get("itemStatus") or "").strip().upper() == "REJECTED":
                    rejected += _qty(blob.get("itemCount") or blob.get("count"))
            _add_qty(by_key, sku, max(qty - rejected, 0))
    return {sku: qty for sku, qty in by_key.values()}


def fetch_fby_in_transit_from_supplies(api: YandexFboApi) -> dict[str, int]:
    by_key: dict[str, tuple[str, int]] = {}
    for supply in api.list_child_supplies():
        if not supply_request_is_in_transit(str(supply.get("status") or "")):
            continue
        request_id = int(supply.get("request_id") or 0)
        if request_id <= 0:
            continue
        for item in api.get_items(request_id):
            sku = str(item.get("sku") or "").strip()
            if not sku:
                continue
            _add_qty(by_key, sku, _qty(item.get("planned_qty")))
    return {sku: qty for sku, qty in by_key.values()}


def build_yandex_stock_rows(
    available: dict[str, int],
    in_transit: dict[str, int],
    catalog_by_sku: dict[str, dict[str, str]],
) -> list[StockByWarehouseRow]:
    stock_by_key = {
        str(sku).casefold(): (str(sku), int(qty))
        for sku, qty in (available or {}).items()
        if str(sku).strip()
    }
    transit_by_key = {
        str(sku).casefold(): (str(sku), int(qty))
        for sku, qty in (in_transit or {}).items()
        if str(sku).strip()
    }
    rows: list[StockByWarehouseRow] = []
    for key in sorted(set(stock_by_key) | set(transit_by_key)):
        sku = stock_by_key.get(key, transit_by_key.get(key, (key, 0)))[0]
        stock_qty = stock_by_key.get(key, (sku, 0))[1]
        transit_qty = transit_by_key.get(key, (sku, 0))[1]
        if stock_qty <= 0 and transit_qty <= 0:
            continue
        catalog = catalog_by_sku.get(key) or {}
        display_sku = str(catalog.get("sku") or sku).strip() or sku
        name = str(catalog.get("name") or "").strip() or MISSING_NAME
        rows.append(
            StockByWarehouseRow(
                sku=display_sku,
                name=name,
                quantity=int(stock_qty),
                in_transit=int(transit_qty),
                total=int(stock_qty) + int(transit_qty),
            )
        )
    return rows


def _require_api(api: YandexFboApi | None) -> YandexFboApi:
    if api is None or not api.is_configured() or not is_value_configured(api.api_key):
        raise ValueError("Не задан YANDEX_API_KEY")
    return api


def build_yandex_product_demand_report(
    catalog_repo: CatalogRepository,
    *,
    api: YandexFboApi | None = None,
    fetch_warehouses: FetchWarehousesFn | None = None,
    fetch_in_transit: FetchInTransitFn | None = None,
    fetch_sales: FetchSalesFn | None = None,
    today: date | None = None,
) -> ProductDemandResult:
    date_from, date_to = sales_window_30_days(today=today)
    if fetch_warehouses is not None:
        warehouses = fetch_warehouses()
    else:
        client = _require_api(api)
        try:
            warehouses = client.list_offer_stocks()
        except ValueError as exc:
            raise ValueError(
                "Не удалось получить остатки FBY Яндекс Маркета. Проверьте YANDEX_API_KEY."
            ) from exc
    available, quarantine = aggregate_fby_stocks(warehouses)
    if fetch_in_transit is not None:
        supply_transit = fetch_in_transit()
    elif fetch_warehouses is not None:
        supply_transit = {}
    else:
        client = _require_api(api)
        try:
            supply_transit = fetch_fby_in_transit_from_supplies(client)
        except ValueError as exc:
            raise ValueError(
                "Не удалось получить поставки FBY Яндекс Маркета. Проверьте YANDEX_API_KEY."
            ) from exc
    in_transit = merge_sku_quantities(quarantine, supply_transit)
    if fetch_sales is not None:
        sales = fetch_sales(date_from, date_to)
    elif fetch_warehouses is not None:
        sales = {}
    else:
        client = _require_api(api)
        try:
            orders = client.list_order_stats(date_from.isoformat(), date_to.isoformat())
        except ValueError as exc:
            raise ValueError(
                "Не удалось получить заказы FBY Яндекс Маркета. Проверьте YANDEX_API_KEY."
            ) from exc
        sales = aggregate_fby_sales(orders)
        logger.info(
            "yandex fby demand sales orders=%s skus=%s period=%s..%s",
            len(orders),
            len(sales),
            date_from,
            date_to,
        )
    catalog_by_sku = _catalog_sku_map(catalog_repo)
    stock_rows = build_yandex_stock_rows(available, in_transit, catalog_by_sku)
    base_rows = expand_demand_stock_rows(stock_rows, sales, catalog_by_sku)
    rows = attach_demand_columns(base_rows, sales)
    missing = sum(1 for row in rows if row.name == MISSING_NAME)
    workbook = build_demand_workbook(
        rows,
        source_title=SOURCE_TITLE,
        warehouse_title=WAREHOUSE_TITLE,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
    )
    return ProductDemandResult(
        source_id=SOURCE_ID,
        source_title=SOURCE_TITLE,
        warehouse_title=WAREHOUSE_TITLE,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        rows=rows,
        total_quantity=sum(row.quantity for row in rows),
        total_in_transit=sum(row.in_transit for row in rows),
        total_all=sum(row.total for row in rows),
        total_sales_30=sum(row.sales_30 for row in rows),
        total_remain_30=sum(row.remain_30 for row in rows),
        total_sales_60=sum(row.sales_60 for row in rows),
        total_remain_60=sum(row.remain_60 for row in rows),
        total_sales_90=sum(row.sales_90 for row in rows),
        total_remain_90=sum(row.remain_90 for row in rows),
        missing_name_count=missing,
        workbook_bytes=workbook,
        filename=FILENAME,
    )
