"""Отчёт потребности: остатки WB плюс продажи за 30/60/90 дней."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.product_demand_report import (
    aggregate_funnel_sales_by_sku,
    attach_demand_columns,
    build_product_demand_report,
    expand_demand_stock_rows,
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


def test_aggregate_funnel_sales_sums_order_count() -> None:
    cards = [
        {
            "product": {"vendorCode": "SS100"},
            "statistic": {"selected": {"orderCount": 4, "buyoutCount": 1}},
        },
        {
            "product": {"vendorCode": "ss100"},
            "statistic": {"selected": {"orderCount": 6}},
        },
        {
            "product": {"vendorCode": "SS200"},
            "statistic": {"selected": {"orderCount": 0}},
        },
    ]
    assert aggregate_funnel_sales_by_sku(cards) == {"SS100": 10}


def test_attach_demand_is_sales_minus_stock() -> None:
    rows = attach_demand_columns(
        [
            StockByWarehouseRow(sku="SS100", name="Case", quantity=13, in_transit=5, total=18),
            StockByWarehouseRow(sku="NEW-SKU", name=MISSING_NAME, quantity=7, in_transit=0, total=7),
        ],
        {"ss100": 10},
    )
    assert [(row.sku, row.sales_30, row.remain_30, row.sales_60, row.remain_60, row.sales_90, row.remain_90) for row in rows] == [
        ("SS100", 10, -8, 20, 2, 30, 12),
        ("NEW-SKU", 0, -7, 0, -7, 0, -7),
    ]


def test_expand_demand_adds_catalog_and_sales_without_stock() -> None:
    rows = expand_demand_stock_rows(
        [StockByWarehouseRow(sku="SS100", name="Case", quantity=13, in_transit=5, total=18)],
        {"SS100": 10, "ghost": 99},
        {
            "ss100": {"sku": "SS100", "name": "Case"},
            "ss300": {"sku": "SS300", "name": "CatalogOnly"},
        },
    )
    assert [(row.sku, row.name, row.quantity, row.total) for row in rows] == [
        ("ghost", MISSING_NAME, 0, 0),
        ("SS100", "Case", 13, 18),
        ("SS300", "CatalogOnly", 0, 0),
    ]


def test_demand_report_includes_catalog_and_sales_without_stock(db_url: str) -> None:
    catalog = _catalog(db_url)
    catalog.create_product({"name": "NanoGlass", "sku": "SS100", "code": "00100"})
    catalog.create_product({"name": "CatalogOnly", "sku": "SS300", "code": "00300"})
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
        ("ghost", 0, 0, 0, 99, 99, 198, 198, 297, 297),
        ("NEW-SKU", 7, 0, 7, 0, -7, 0, -7, 0, -7),
        ("SS100", 13, 5, 18, 10, -8, 20, 2, 30, 12),
        ("SS300", 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ]
    assert result.date_from == "2026-09-29"
    assert result.date_to == "2026-10-28"
    assert result.total_sales_30 == 109
    assert result.total_remain_90 == 302
    assert result.missing_name_count == 2
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
        "Продажи минус остатки за 30 дней",
        "Продажи за 60 дней",
        "Продажи минус остатки за 60 дней",
        "Продажи за 90 дней",
        "Продажи минус остатки за 90 дней",
    ]
    assert [cell.value for cell in sheet[6]] == ["ghost", MISSING_NAME, 0, 0, 0, 99, 99, 198, 198, 297, 297]
    assert [cell.value for cell in sheet[7]] == ["NEW-SKU", MISSING_NAME, 7, 0, 7, 0, -7, 0, -7, 0, -7]
    assert [cell.value for cell in sheet[8]] == ["SS100", "NanoGlass", 13, 5, 18, 10, -8, 20, 2, 30, 12]
    assert [cell.value for cell in sheet[9]] == ["SS300", "CatalogOnly", 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert sheet[10][0].value == "Итого"
    assert [sheet[10][n].value for n in range(2, 11)] == [20, 5, 25, 109, 84, 218, 193, 327, 302]
