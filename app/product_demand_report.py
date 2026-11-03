"""Отчёт «Потребность товаров»: остатки на складах плюс заказы WB за 30/60/90 дней."""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.adapters.base import is_value_configured
from app.catalog_repository import CatalogRepository
from app.stock_by_warehouse_report import (
    ANALYTICS_BASE,
    MISSING_NAME,
    FetchInTransitFn,
    FetchRawFn,
    StockByWarehouseRow,
    build_stock_by_warehouse_report,
)

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("Europe/Moscow")
_FUNNEL_LIMIT = 1000
_FUNNEL_MAX_OFFSET = 20000
_FUNNEL_INTERVAL_SEC = 21
_HEADERS = (
    "Артикул",
    "Название",
    "Количество",
    "В пути",
    "Всего",
    "Продажи за 30 дней",
    "Продажи минус остатки за 30 дней",
    "Продажи за 60 дней",
    "Продажи минус остатки за 60 дней",
    "Продажи за 90 дней",
    "Продажи минус остатки за 90 дней",
)

FetchSalesFn = Callable[[str, date, date], dict[str, int]]


@dataclass(frozen=True)
class ProductDemandRow:
    sku: str
    name: str
    quantity: int
    in_transit: int
    total: int
    sales_30: int
    remain_30: int
    sales_60: int
    remain_60: int
    sales_90: int
    remain_90: int


@dataclass(frozen=True)
class ProductDemandResult:
    source_id: str
    source_title: str
    warehouse_title: str
    date_from: str
    date_to: str
    rows: list[ProductDemandRow]
    total_quantity: int
    total_in_transit: int
    total_all: int
    total_sales_30: int
    total_remain_30: int
    total_sales_60: int
    total_remain_60: int
    total_sales_90: int
    total_remain_90: int
    missing_name_count: int
    workbook_bytes: bytes
    filename: str


def _excel_safe_text(value: object) -> str:
    text = str(value or "")
    return "".join(ch for ch in text if ord(ch) >= 32 or ch in "\t\n\r")


def _qty(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    data = _as_dict(raw)
    for key in ("products", "cards", "data", "report"):
        inner = data.get(key)
        if isinstance(inner, list):
            return inner
        nested = _as_dict(inner)
        for nested_key in ("products", "cards", "report"):
            nested_list = nested.get(nested_key)
            if isinstance(nested_list, list):
                return nested_list
    return []


def _http_error(response: requests.Response) -> requests.HTTPError:
    body = (response.text or "").strip()
    if len(body) > 800:
        body = body[:800] + "…"
    return requests.HTTPError(
        f"{response.status_code} {response.reason} for {response.url} — {body}",
        response=response,
    )


def sales_window_30_days(*, today: date | None = None) -> tuple[date, date]:
    end = today or datetime.now(_TZ).date()
    start = end - timedelta(days=29)
    return start, end


def aggregate_funnel_sales_by_sku(cards: list[dict[str, Any]]) -> dict[str, int]:
    by_key: dict[str, tuple[str, int]] = {}
    for card in cards:
        product = _as_dict(card.get("product") or card.get("nm") or card)
        sku = str(
            product.get("vendorCode")
            or card.get("vendorCode")
            or product.get("saName")
            or ""
        ).strip()
        if not sku:
            continue
        statistic = _as_dict(card.get("statistic") or card.get("statistics"))
        stats = _as_dict(
            statistic.get("selected")
            or statistic.get("selectedPeriod")
            or card.get("currentPeriod")
            or card.get("selectedPeriod")
            or card.get("period")
        )
        qty = _qty(
            stats.get("orderCount")
            if stats.get("orderCount") is not None
            else stats.get("ordersCount")
            if stats.get("ordersCount") is not None
            else stats.get("orders")
        )
        if qty == 0:
            continue
        key = sku.casefold()
        prev_sku, prev_qty = by_key.get(key, (sku, 0))
        by_key[key] = (prev_sku, prev_qty + qty)
    return {sku: qty for sku, qty in by_key.values()}


def fetch_wb_sales_by_sku(token: str, date_from: date, date_to: date) -> dict[str, int]:
    if not is_value_configured(token):
        raise ValueError("Не задан WB_API_TOKEN")
    headers = {"Authorization": token, "Content-Type": "application/json"}
    cards: list[dict[str, Any]] = []
    offset = 0
    try:
        while offset < _FUNNEL_MAX_OFFSET:
            body = {
                "selectedPeriod": {
                    "start": date_from.isoformat(),
                    "end": date_to.isoformat(),
                },
                "limit": _FUNNEL_LIMIT,
                "offset": offset,
                "skipDeletedNm": True,
                "orderBy": {"field": "orderCount", "mode": "desc"},
            }
            for attempt in range(5):
                if offset > 0 or attempt > 0:
                    time.sleep(_FUNNEL_INTERVAL_SEC)
                response = requests.post(
                    f"{ANALYTICS_BASE}/api/analytics/v3/sales-funnel/products",
                    headers=headers,
                    json=body,
                    timeout=90,
                )
                if response.status_code == 429:
                    continue
                if not response.ok:
                    raise _http_error(response)
                payload = response.json() if (response.content or b"").strip() else {}
                break
            else:
                raise ValueError(
                    "WB ограничивает частоту запросов к воронке продаж. Повторите через минуту."
                )
            batch = [item for item in _as_list(payload) if isinstance(item, dict)]
            if not batch:
                break
            cards.extend(batch)
            if len(batch) < _FUNNEL_LIMIT:
                break
            offset += len(batch)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            raise ValueError(
                "Нет доступа к аналитике WB. Нужен токен с категорией «Аналитика»."
            ) from exc
        raise RuntimeError(str(exc)) from exc
    sales = aggregate_funnel_sales_by_sku(cards)
    logger.info(
        "wb funnel sales cards=%s skus=%s period=%s..%s",
        len(cards),
        len(sales),
        date_from,
        date_to,
    )
    return sales


def _catalog_sku_map(catalog_repo: CatalogRepository) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for product in catalog_repo.list_products({}):
        sku = str(getattr(product, "sku", "") or "").strip()
        if not sku:
            continue
        out[sku.casefold()] = {
            "sku": sku,
            "name": str(getattr(product, "name", "") or "").strip(),
        }
    return out


def expand_demand_stock_rows(
    stock_rows: list[StockByWarehouseRow],
    sales_by_sku: dict[str, int],
    catalog_by_sku: dict[str, dict[str, str]],
) -> list[StockByWarehouseRow]:
    by_key: dict[str, StockByWarehouseRow] = {}
    for row in stock_rows:
        key = str(row.sku or "").strip().casefold()
        if not key:
            continue
        by_key[key] = row
    for key, item in (catalog_by_sku or {}).items():
        sku_key = str(key or "").strip().casefold()
        if not sku_key or sku_key in by_key:
            continue
        sku = str(item.get("sku") or key).strip() or key
        name = str(item.get("name") or "").strip() or MISSING_NAME
        by_key[sku_key] = StockByWarehouseRow(
            sku=sku,
            name=name,
            quantity=0,
            in_transit=0,
            total=0,
        )
    for sku, _qty in (sales_by_sku or {}).items():
        sku_key = str(sku or "").strip().casefold()
        if not sku_key or sku_key in by_key:
            continue
        catalog = (catalog_by_sku or {}).get(sku_key) or {}
        display_sku = str(catalog.get("sku") or sku).strip() or sku
        name = str(catalog.get("name") or "").strip() or MISSING_NAME
        by_key[sku_key] = StockByWarehouseRow(
            sku=display_sku,
            name=name,
            quantity=0,
            in_transit=0,
            total=0,
        )
    return [by_key[key] for key in sorted(by_key)]


def attach_demand_columns(
    rows: list[StockByWarehouseRow],
    sales_by_sku: dict[str, int],
) -> list[ProductDemandRow]:
    sales_map = {str(sku).casefold(): int(qty) for sku, qty in (sales_by_sku or {}).items()}
    out: list[ProductDemandRow] = []
    for row in rows:
        sales_30 = int(sales_map.get(str(row.sku).casefold(), 0) or 0)
        sales_60 = sales_30 * 2
        sales_90 = sales_30 * 3
        total = int(row.total)
        out.append(
            ProductDemandRow(
                sku=row.sku,
                name=row.name,
                quantity=int(row.quantity),
                in_transit=int(row.in_transit),
                total=total,
                sales_30=sales_30,
                remain_30=sales_30 - total,
                sales_60=sales_60,
                remain_60=sales_60 - total,
                sales_90=sales_90,
                remain_90=sales_90 - total,
            )
        )
    return out


def build_demand_workbook(
    rows: list[ProductDemandRow],
    *,
    source_title: str,
    warehouse_title: str,
    date_from: str,
    date_to: str,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Потребность"
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E8EEF4")
    thin = Border(
        left=Side(style="thin", color="D8DEE9"),
        right=Side(style="thin", color="D8DEE9"),
        top=Side(style="thin", color="D8DEE9"),
        bottom=Side(style="thin", color="D8DEE9"),
    )
    ncols = len(_HEADERS)
    ws.append(["Источник", _excel_safe_text(source_title)])
    ws.append(["Склад", _excel_safe_text(warehouse_title)])
    ws.append(["Продажи за 30 дней", f"{date_from} — {date_to}"])
    ws.append([])
    ws.append(list(_HEADERS))
    header_row = ws.max_row
    for col in range(1, ncols + 1):
        cell = ws.cell(row=header_row, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    ws.row_dimensions[header_row].height = 32
    totals = [0] * 9
    for row in rows:
        values = [
            int(row.quantity),
            int(row.in_transit),
            int(row.total),
            int(row.sales_30),
            int(row.remain_30),
            int(row.sales_60),
            int(row.remain_60),
            int(row.sales_90),
            int(row.remain_90),
        ]
        ws.append([_excel_safe_text(row.sku), _excel_safe_text(row.name), *values])
        excel_row = ws.max_row
        for col in range(1, ncols + 1):
            ws.cell(row=excel_row, column=col).border = thin
        for i, value in enumerate(values):
            totals[i] += value
    if rows:
        ws.append(["Итого", "", *totals])
        total_row = ws.max_row
        for col in range(1, ncols + 1):
            cell = ws.cell(row=total_row, column=col)
            cell.font = header_font
            cell.border = thin
        for col in range(3, ncols + 1):
            ws.cell(row=total_row, column=col).alignment = Alignment(horizontal="right")
    ws.column_dimensions[get_column_letter(1)].width = 22
    ws.column_dimensions[get_column_letter(2)].width = 56
    for col in range(3, 6):
        ws.column_dimensions[get_column_letter(col)].width = 14
    for col in range(6, ncols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 28
    last_data_row = max(header_row, len(rows) + header_row)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncols)}{last_data_row}"
    ws.freeze_panes = f"A{header_row + 1}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_product_demand_report(
    catalog_repo: CatalogRepository,
    *,
    source: str,
    wb_api_token: str = "",
    fetch_wb_raw: FetchRawFn | None = None,
    fetch_wb_in_transit: FetchInTransitFn | None = None,
    fetch_wb_sales: FetchSalesFn | None = None,
    today: date | None = None,
) -> ProductDemandResult:
    stock = build_stock_by_warehouse_report(
        catalog_repo,
        source=source,
        wb_api_token=wb_api_token,
        fetch_wb_raw=fetch_wb_raw,
        fetch_wb_in_transit=fetch_wb_in_transit,
    )
    date_from, date_to = sales_window_30_days(today=today)
    if fetch_wb_sales is not None:
        sales = fetch_wb_sales(wb_api_token, date_from, date_to)
    elif fetch_wb_raw is not None:
        sales = {}
    else:
        sales = fetch_wb_sales_by_sku(wb_api_token, date_from, date_to)
    base_rows = expand_demand_stock_rows(stock.rows, sales, _catalog_sku_map(catalog_repo))
    rows = attach_demand_columns(base_rows, sales)
    missing = sum(1 for row in rows if row.name == MISSING_NAME)
    workbook = build_demand_workbook(
        rows,
        source_title=stock.source_title,
        warehouse_title=stock.warehouse_title,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
    )
    return ProductDemandResult(
        source_id=stock.source_id,
        source_title=stock.source_title,
        warehouse_title=stock.warehouse_title,
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
        filename="potrebnost_tovarov_wb.xlsx",
    )
