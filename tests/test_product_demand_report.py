"""Отчёт потребности: остатки WB плюс продажи за 30/60/90 дней."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.product_demand_report import (
    aggregate_sales_by_sku,
    attach_demand_columns,
    build_product_demand_report,
    sales_window_30_days,
)
from app.stock_by_warehouse_report import MISSING_NAME, WB_RF_WAREHOUSE, StockByWarehouseRow


def _catalog(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def _raw_rows() -> list[dict]:
    return [
        {
            "vendorCode": "SS100",
            "warehouses": [
                {"warehouseName": "Коледино", "quantity": 40},
                {"warehouseName": WB_RF_WAREHOUSE, "quantity": 10},
            ],
        },
        {
            "vendorCode": "SS100",
            "techSize": "2",
            "warehouses": [{"warehouseName": WB_RF_WAREHOUSE, "quantity": 3}],
        },
        {
            "vendorCode": "NEW-SKU",
            "warehouses": [{"warehouseName": WB_RF_WAREHOUSE, "quantity": 7}],
        },
    ]


def test_sales_window_is_30_inclusive_days() -> None:
    start, end = sales_window_30_days(today=date(2026, 10, 28))
    assert start == date(2026, 9, 29)
    assert end == date(2026, 10, 28)


def test_aggregate_sales_subtracts_returns() -> None:
    rows = [
        {"supplierArticle": "SS100", "date": "2026-10-10T12:00:00+03:00", "quantity": 4, "saleID": "S1"},
        {"supplierArticle": "ss100", "date": "2026-10-11T12:00:00+03:00", "quantity": 1, "saleID": "R2"},
        {"supplierArticle": "SS200", "date": "2026-08-01T12:00:00+03:00", "quantity": 9, "saleID": "S3"},
        {"supplierArticle": "SS300", "date": "2026-10-12T12:00:00+03:00", "quantity": 2, "orderType": "Возврат"},
    ]
    result = aggregate_sales_by_sku(
        rows, date_from=date(2026, 9, 29), date_to=date(2026, 10, 28)
    )
    assert result == {"SS100": 3, "SS300": -2}


def test_attach_demand_allows_negative_remainder() -> None:
    rows = attach_demand_columns(
        [
            StockByWarehouseRow(sku="SS100", name="Case", quantity=13, in_transit=5, total=18),
            StockByWarehouseRow(sku="NEW-SKU", name=MISSING_NAME, quantity=7, in_transit=0, total=7),
        ],
        {"ss100": 10},
    )
    assert [(row.sku, row.sales_30, row.remain_30, row.sales_60, row.remain_60, row.sales_90, row.remain_90) for row in rows] == [
        ("SS100", 10, 8, 20, -2, 30, -12),
        ("NEW-SKU", 0, 7, 0, 7, 0, 7),
    ]


def test_demand_report_keeps_stock_rows_and_adds_sales(db_url: str) -> None:
    catalog = _catalog(db_url)
    catalog.create_product({"name": "NanoGlass", "sku": "SS100", "code": "00100"})
    result = build_product_demand_report(
        catalog,
        source="wb",
        wb_api_token="token",
        fetch_wb_raw=lambda _token: _raw_rows(),
        fetch_wb_in_transit=lambda _token: {"SS100": 5},
        fetch_wb_sales=lambda _token, _start, _end: {"SS100": 10, "ghost": 99},
        today=date(2026, 10, 28),
    )
    assert [(row.sku, row.quantity, row.in_transit, row.total, row.sales_30, row.remain_30, row.sales_60, row.remain_60, row.sales_90, row.remain_90) for row in result.rows] == [
        ("NEW-SKU", 7, 0, 7, 0, 7, 0, 7, 0, 7),
        ("SS100", 13, 5, 18, 10, 8, 20, -2, 30, -12),
    ]
    assert result.date_from == "2026-09-29"
    assert result.date_to == "2026-10-28"
    assert result.total_sales_30 == 10
    assert result.total_remain_90 == -5
    assert result.filename == "potrebnost_tovarov_wb.xlsx"

    book = load_workbook(BytesIO(result.workbook_bytes))
    sheet = book.active
    assert sheet.title == "Потребность"
    assert [cell.value for cell in sheet[1]][:2] == ["Источник", "Склады WB"]
    assert [cell.value for cell in sheet[3]][:2] == ["Продажи за 30 дней", "2026-09-29 — 2026-10-28"]
    assert [cell.value for cell in sheet[5]] == [
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
    ]
    assert [cell.value for cell in sheet[6]] == ["NEW-SKU", MISSING_NAME, 7, 0, 7, 0, 7, 0, 7, 0, 7]
    assert [cell.value for cell in sheet[7]] == ["SS100", "NanoGlass", 13, 5, 18, 10, 8, 20, -2, 30, -12]
    assert sheet[8][0].value == "Итого"
    assert [sheet[8][n].value for n in range(2, 11)] == [20, 5, 25, 10, 15, 20, 5, 30, -5]
