"""Отчёт потребности Яндекса: сумма FBY по всем кластерам плюс продажи 30/60/90."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.stock_by_warehouse_report import MISSING_NAME
from app.yandex_demand_report import (
    SOURCE_TITLE,
    WAREHOUSE_TITLE,
    aggregate_fby_sales,
    aggregate_fby_stocks,
    build_yandex_product_demand_report,
    merge_sku_quantities,
)
from app.yandex_fbo_api import supply_request_is_in_transit


def _catalog(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def _warehouses() -> list[dict]:
    return [
        {
            "warehouseId": 1,
            "offers": [
                {
                    "offerId": "SS100",
                    "stocks": [
                        {"type": "AVAILABLE", "count": 10},
                        {"type": "FREEZE", "count": 2},
                        {"type": "FIT", "count": 12},
                        {"type": "QUARANTINE", "count": 3},
                    ],
                },
                {
                    "offerId": "SS200",
                    "stocks": [{"type": "AVAILABLE", "count": 4}],
                },
            ],
        },
        {
            "warehouseId": 2,
            "offers": [
                {
                    "offerId": "ss100",
                    "stocks": [
                        {"type": "AVAILABLE", "count": 5},
                        {"type": "QUARANTINE", "count": 1},
                    ],
                }
            ],
        },
    ]


def test_supply_in_transit_skips_finished_and_cancelled() -> None:
    assert supply_request_is_in_transit("ACCEPTED_AT_WAREHOUSE") is True
    assert supply_request_is_in_transit("SHIPPED") is True
    assert supply_request_is_in_transit("FINISHED") is False
    assert supply_request_is_in_transit("CANCELLED") is False
    assert supply_request_is_in_transit("cancelled_by_partner") is False


def test_aggregate_fby_stocks_sums_available_and_quarantine_across_warehouses() -> None:
    available, quarantine = aggregate_fby_stocks(_warehouses())
    assert available == {"SS100": 15, "SS200": 4}
    assert quarantine == {"SS100": 4}


def test_aggregate_fby_sales_skips_cancelled_and_rejected() -> None:
    orders = [
        {
            "status": "DELIVERED",
            "items": [{"shopSku": "SS100", "count": 4}],
        },
        {
            "status": "PROCESSING",
            "items": [{"shopSku": "ss100", "count": 6}],
        },
        {
            "status": "CANCELLED_IN_PROCESSING",
            "items": [{"shopSku": "SS100", "count": 99}],
        },
        {
            "status": "DELIVERY",
            "items": [
                {
                    "shopSku": "SS200",
                    "count": 5,
                    "details": [{"itemStatus": "REJECTED", "itemCount": 2}],
                }
            ],
        },
        {
            "status": "UNPAID",
            "items": [{"shopSku": "SS200", "count": 10}],
        },
    ]
    assert aggregate_fby_sales(orders) == {"SS100": 10, "SS200": 3}


def test_merge_sku_quantities_is_case_insensitive() -> None:
    assert merge_sku_quantities({"SS100": 3}, {"ss100": 1, "SS200": 2}) == {
        "SS100": 4,
        "SS200": 2,
    }


def test_yandex_demand_report_sums_clusters_and_demand_columns(db_url: str) -> None:
    catalog = _catalog(db_url)
    catalog.create_product({"name": "NanoGlass", "sku": "SS100", "code": "00100"})
    catalog.create_product({"name": "CatalogOnly", "sku": "SS300", "code": "00300"})
    result = build_yandex_product_demand_report(
        catalog,
        fetch_warehouses=_warehouses,
        fetch_in_transit=lambda: {"SS100": 5, "ghost": 2},
        fetch_sales=lambda _start, _end: {"SS100": 10, "ghost": 99},
        today=date(2026, 10, 28),
    )
    assert [
        (
            row.sku,
            row.quantity,
            row.in_transit,
            row.total,
            row.sales_30,
            row.remain_30,
            row.sales_60,
            row.remain_60,
            row.sales_90,
            row.remain_90,
        )
        for row in result.rows
    ] == [
        ("ghost", 0, 2, 2, 99, 97, 198, 196, 297, 295),
        ("SS100", 15, 9, 24, 10, -14, 20, -4, 30, 6),
        ("SS200", 4, 0, 4, 0, -4, 0, -4, 0, -4),
        ("SS300", 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ]
    assert result.source_title == SOURCE_TITLE
    assert result.warehouse_title == WAREHOUSE_TITLE
    assert result.date_from == "2026-09-29"
    assert result.date_to == "2026-10-28"
    assert result.filename == "potrebnost_tovarov_yandex.xlsx"
    assert result.total_sales_30 == 109
    assert result.missing_name_count == 2

    book = load_workbook(BytesIO(result.workbook_bytes))
    sheet = book.active
    assert sheet.title == "Потребность"
    assert [cell.value for cell in sheet[1]][:2] == ["Источник", SOURCE_TITLE]
    assert [cell.value for cell in sheet[2]][:2] == ["Склад", WAREHOUSE_TITLE]
    assert [cell.value for cell in sheet[6]] == ["ghost", MISSING_NAME, 0, 2, 2, 99, 97, 198, 196, 297, 295]
    assert [cell.value for cell in sheet[7]] == [
        "SS100",
        "NanoGlass",
        15,
        9,
        24,
        10,
        -14,
        20,
        -4,
        30,
        6,
    ]
