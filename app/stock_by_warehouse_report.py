"""Отчёт «Остатки товаров на складах»: сейчас только склады WB (колонка «Склад WB РФ»)."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.adapters.base import is_value_configured
from app.catalog_repository import CatalogRepository

ANALYTICS_BASE = "https://seller-analytics-api.wildberries.ru"
WB_RF_WAREHOUSE = "Склад WB РФ"
MISSING_NAME = "Товар не найден в каталоге"

SOURCES: tuple[dict[str, Any], ...] = (
    {"id": "own", "title": "Среди своих складов", "implemented": False},
    {"id": "wb", "title": "Склады WB", "implemented": True},
    {"id": "yandex", "title": "Склады Яндекса", "implemented": False},
    {"id": "ozon", "title": "Склады Ozon", "implemented": False},
)
SOURCE_IDS = frozenset(item["id"] for item in SOURCES)
_HEADERS = ("Артикул", "Название", "Количество")

FetchRawFn = Callable[[str], list[dict[str, Any]]]


@dataclass(frozen=True)
class StockByWarehouseRow:
    sku: str
    name: str
    quantity: int


@dataclass(frozen=True)
class StockByWarehouseResult:
    source_id: str
    source_title: str
    warehouse_title: str
    rows: list[StockByWarehouseRow]
    total_quantity: int
    missing_name_count: int
    workbook_bytes: bytes
    filename: str


def source_meta() -> list[dict[str, Any]]:
    return [dict(item) for item in SOURCES]


def _excel_safe_text(value: object) -> str:
    text = str(value or "")
    return "".join(ch for ch in text if ord(ch) >= 32 or ch in "\t\n\r")


def _qty(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("data", "report", "reports", "stocks", "items"):
            inner = raw.get(key)
            if isinstance(inner, list):
                return inner
    return []


def _http_error(response: requests.Response) -> requests.HTTPError:
    body = (response.text or "").strip()
    if len(body) > 800:
        body = body[:800] + "…"
    return requests.HTTPError(
        f"{response.status_code} {response.reason} for {response.url} — {body}",
        response=response,
    )


def _json(response: requests.Response) -> Any:
    if response.status_code == 204:
        return None
    if response.status_code == 429:
        raise ValueError("WB принимает этот отчёт не чаще раза в минуту. Повторите через минуту.")
    if not response.ok:
        raise _http_error(response)
    if not (response.content or b"").strip():
        return None
    return response.json()


def _task_id(payload: Any) -> str:
    data = _as_dict(payload)
    nested = _as_dict(data.get("data"))
    for blob in (nested, data):
        for key in ("taskId", "task_id", "id"):
            value = str(blob.get(key) or "").strip()
            if value:
                return value
    raise RuntimeError("WB не вернул идентификатор отчёта остатков")


def _task_status(payload: Any) -> str:
    data = _as_dict(payload)
    nested = _as_dict(data.get("data"))
    for blob in (nested, data):
        value = str(blob.get("status") or blob.get("Status") or "").strip().lower()
        if value:
            return value
    return ""


def fetch_wb_warehouse_remains(token: str, *, timeout_sec: int = 180) -> list[dict[str, Any]]:
    if not is_value_configured(token):
        raise ValueError("Не задан WB_API_TOKEN")
    headers = {"Authorization": token}
    params = {
        "locale": "ru",
        "groupByBrand": "true",
        "groupBySubject": "true",
        "groupBySa": "true",
        "groupByNm": "true",
        "groupByBarcode": "true",
        "groupBySize": "true",
    }
    try:
        created = requests.get(
            f"{ANALYTICS_BASE}/api/v1/warehouse_remains",
            headers=headers,
            params=params,
            timeout=60,
        )
        task_id = _task_id(_json(created))
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403):
            raise ValueError(
                "Нет доступа к аналитике WB. Нужен токен с категорией «Аналитика»."
            ) from exc
        raise RuntimeError(str(exc)) from exc
    status_url = f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/status"
    deadline = time.monotonic() + timeout_sec
    last = ""
    try:
        while time.monotonic() < deadline:
            status_resp = requests.get(status_url, headers=headers, timeout=60)
            if status_resp.status_code == 429:
                time.sleep(6)
                continue
            last = _task_status(_json(status_resp))
            if last in {"done", "success", "ready"}:
                break
            if last in {"canceled", "cancelled", "purged", "error", "failed"}:
                raise RuntimeError(f"Отчёт WB завершился со статусом {last}")
            time.sleep(6)
        else:
            raise TimeoutError(f"Отчёт WB не готов за {timeout_sec} с")
        download = requests.get(
            f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/download",
            headers=headers,
            timeout=120,
        )
        payload = _json(download)
    except requests.HTTPError as exc:
        raise RuntimeError(str(exc)) from exc
    return [item for item in _as_list(payload) if isinstance(item, dict)]


def aggregate_wb_rf_quantities(raw_rows: list[dict[str, Any]]) -> dict[str, int]:
    wanted = WB_RF_WAREHOUSE.casefold()
    by_key: dict[str, tuple[str, int]] = {}
    for row in raw_rows:
        sku = str(row.get("vendorCode") or row.get("supplierArticle") or "").strip()
        if not sku:
            continue
        qty = 0
        warehouses = row.get("warehouses")
        if isinstance(warehouses, list):
            for item in warehouses:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("warehouseName") or "").strip()
                if name.casefold() == wanted:
                    qty += _qty(item.get("quantity"))
        else:
            name = str(row.get("warehouseName") or "").strip()
            if name.casefold() == wanted:
                qty = _qty(
                    row.get("quantity") if row.get("quantity") is not None else row.get("quantityFull")
                )
        if qty <= 0:
            continue
        key = sku.casefold()
        prev_sku, prev_qty = by_key.get(key, (sku, 0))
        by_key[key] = (prev_sku, prev_qty + qty)
    return {sku: qty for sku, qty in by_key.values()}


def build_wb_stock_rows(
    raw_rows: list[dict[str, Any]],
    catalog_by_sku: dict[str, dict[str, Any]],
) -> list[StockByWarehouseRow]:
    quantities = aggregate_wb_rf_quantities(raw_rows)
    rows: list[StockByWarehouseRow] = []
    for sku, qty in sorted(quantities.items(), key=lambda item: item[0].casefold()):
        catalog = catalog_by_sku.get(sku.casefold())
        if catalog:
            display_sku = str(catalog.get("sku") or sku).strip() or sku
            name = str(catalog.get("name") or "").strip() or MISSING_NAME
        else:
            display_sku = sku
            name = MISSING_NAME
        rows.append(StockByWarehouseRow(sku=display_sku, name=name, quantity=int(qty)))
    return rows


def build_stock_workbook(
    rows: list[StockByWarehouseRow],
    *,
    source_title: str,
    warehouse_title: str,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Остатки"
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E8EEF4")
    thin = Border(
        left=Side(style="thin", color="D8DEE9"),
        right=Side(style="thin", color="D8DEE9"),
        top=Side(style="thin", color="D8DEE9"),
        bottom=Side(style="thin", color="D8DEE9"),
    )
    ws.append(["Источник", _excel_safe_text(source_title)])
    ws.append(["Склад", _excel_safe_text(warehouse_title)])
    ws.append([])
    ws.append(list(_HEADERS))
    for col in range(1, len(_HEADERS) + 1):
        cell = ws.cell(row=4, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin
    total = 0
    for row in rows:
        ws.append([_excel_safe_text(row.sku), _excel_safe_text(row.name), int(row.quantity)])
        excel_row = ws.max_row
        for col in range(1, 4):
            ws.cell(row=excel_row, column=col).border = thin
        total += int(row.quantity)
    if rows:
        ws.append(["Итого", "", total])
        total_row = ws.max_row
        for col in range(1, 4):
            cell = ws.cell(row=total_row, column=col)
            cell.font = header_font
            cell.border = thin
        ws.cell(row=total_row, column=3).alignment = Alignment(horizontal="right")
    ws.column_dimensions[get_column_letter(1)].width = 22
    ws.column_dimensions[get_column_letter(2)].width = 56
    ws.column_dimensions[get_column_letter(3)].width = 16
    ws.auto_filter.ref = f"A4:C{max(4, len(rows) + 4)}"
    ws.freeze_panes = "A5"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_stock_by_warehouse_report(
    catalog_repo: CatalogRepository,
    *,
    source: str,
    wb_api_token: str = "",
    fetch_wb_raw: FetchRawFn | None = None,
) -> StockByWarehouseResult:
    source_id = str(source or "").strip().lower()
    if source_id not in SOURCE_IDS:
        raise ValueError(
            "Источник должен быть одним из: " + ", ".join(item["title"] for item in SOURCES)
        )
    item = next(row for row in SOURCES if row["id"] == source_id)
    if not item["implemented"]:
        raise ValueError(f"Выгрузка «{item['title']}» пока не реализована")
    if source_id != "wb":
        raise ValueError(f"Выгрузка «{item['title']}» пока не реализована")
    fetcher = fetch_wb_raw or fetch_wb_warehouse_remains
    raw_rows = fetcher(wb_api_token)
    quantities = aggregate_wb_rf_quantities(raw_rows)
    catalog_by_sku = catalog_repo.lookup_products_by_skus(list(quantities))
    rows = build_wb_stock_rows(raw_rows, catalog_by_sku)
    missing = sum(1 for row in rows if row.name == MISSING_NAME)
    title = str(item["title"])
    workbook = build_stock_workbook(rows, source_title=title, warehouse_title=WB_RF_WAREHOUSE)
    return StockByWarehouseResult(
        source_id=source_id,
        source_title=title,
        warehouse_title=WB_RF_WAREHOUSE,
        rows=rows,
        total_quantity=sum(row.quantity for row in rows),
        missing_name_count=missing,
        workbook_bytes=workbook,
        filename="ostatki_sklady_wb.xlsx",
    )
