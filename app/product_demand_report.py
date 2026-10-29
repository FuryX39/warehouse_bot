"""Отчёт «Потребность товаров»: остатки на складах плюс продажи за 30/60/90 дней."""

from __future__ import annotations

import io
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
    FetchInTransitFn,
    FetchRawFn,
    StockByWarehouseRow,
    build_stock_by_warehouse_report,
)

STATS_BASE = "https://statistics-api.wildberries.ru"
_TZ = ZoneInfo("Europe/Moscow")
_SALES_PAGE_LIMIT = 100_000
_SALES_MAX_PAGES = 40
_HEADERS = (
    "Артикул",
    "Название",
    "Количество",
    "В пути",
    "Всего",
    "Продажи за 30 дней",
    "Остатки минус продажи за 30 дней",
    "Продажи за 60 дней",
    "Остатки минус продажи за 60 дней",
    "Продажи за 90 дней",
    "Остатки минус продажи за 90 дней",
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
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
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


def _parse_sale_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TZ)
    return parsed.astimezone(_TZ).date()


def _sale_sign(row: dict[str, Any]) -> int:
    sale_id = str(row.get("saleID") or row.get("saleId") or "").strip()
    if sale_id[:1].casefold() == "r":
        return -1
    order_type = str(row.get("orderType") or "").casefold()
    if "возврат" in order_type:
        return -1
    return 1


def aggregate_sales_by_sku(
    raw_rows: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
) -> dict[str, int]:
    by_key: dict[str, tuple[str, int]] = {}
    for row in raw_rows:
        sku = str(row.get("supplierArticle") or row.get("vendorCode") or "").strip()
        if not sku:
            continue
        sold_on = _parse_sale_date(row.get("date") or row.get("saleDate"))
        if sold_on is None or sold_on < date_from or sold_on > date_to:
            continue
        qty = _qty(row.get("quantity")) * _sale_sign(row)
        if qty == 0:
            continue
        key = sku.casefold()
        prev_sku, prev_qty = by_key.get(key, (sku, 0))
        by_key[key] = (prev_sku, prev_qty + qty)
    return {sku: qty for sku, qty in by_key.values()}


def fetch_wb_sales_by_sku(token: str, date_from: date, date_to: date) -> dict[str, int]:
    if not is_value_configured(token):
        raise ValueError("Не задан WB_API_TOKEN")
    headers = {"Authorization": token}
    date_from_raw = datetime(date_from.year, date_from.month, date_from.day, tzinfo=_TZ).isoformat(
        timespec="seconds"
    )
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = date_from_raw
    try:
        for _ in range(_SALES_MAX_PAGES):
            for attempt in range(5):
                response = requests.get(
                    f"{STATS_BASE}/api/v1/supplier/sales",
                    headers=headers,
                    params={"dateFrom": cursor, "flag": 0},
                    timeout=120,
                )
                if response.status_code == 429:
                    time.sleep(20 * (attempt + 1))
                    continue
                if not response.ok:
                    raise _http_error(response)
                payload = response.json() if (response.content or b"").strip() else []
                break
            else:
                raise ValueError("WB ограничивает частоту запросов к продажам. Повторите через минуту.")
            batch = [item for item in _as_list(payload) if isinstance(item, dict)]
            if not batch:
                break
            new_rows = 0
            last_change = ""
            for item in batch:
                sale_id = str(item.get("saleID") or item.get("rrd_id") or item.get("srid") or "").strip()
                key = sale_id or f"{item.get('date')}:{item.get('supplierArticle')}:{item.get('barcode')}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                new_rows += 1
                last_change = str(item.get("lastChangeDate") or last_change)
            if len(batch) < _SALES_PAGE_LIMIT or not last_change or last_change == cursor or new_rows == 0:
                break
            cursor = last_change
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            raise ValueError(
                "Нет доступа к статистике WB. Нужен токен с категорией «Статистика»."
            ) from exc
        raise RuntimeError(str(exc)) from exc
    return aggregate_sales_by_sku(collected, date_from=date_from, date_to=date_to)


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
                remain_30=total - sales_30,
                sales_60=sales_60,
                remain_60=total - sales_60,
                sales_90=sales_90,
                remain_90=total - sales_90,
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
    rows = attach_demand_columns(stock.rows, sales)
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
        missing_name_count=stock.missing_name_count,
        workbook_bytes=workbook,
        filename="potrebnost_tovarov_wb.xlsx",
    )
