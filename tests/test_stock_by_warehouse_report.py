"""Отчёт остатков на складах: склады WB, колонка «Склад WB РФ»."""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.stock_by_warehouse_report import (
    MISSING_NAME,
    WB_RF_WAREHOUSE,
    build_stock_by_warehouse_report,
)


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
            "vendorCode": "SS200",
            "warehouses": [{"warehouseName": WB_RF_WAREHOUSE, "quantity": 0}],
        },
        {
            "vendorCode": "NEW-SKU",
            "warehouses": [{"warehouseName": WB_RF_WAREHOUSE, "quantity": 7}],
        },
        {
            "vendorCode": "SS300",
            "warehouses": [{"warehouseName": "Электросталь", "quantity": 99}],
        },
    ]


def test_wb_stock_report_matches_catalog_and_sums_rf_warehouse(db_url: str) -> None:
    catalog = _catalog(db_url)
    catalog.create_product({"name": "NanoGlass", "sku": "SS100", "code": "00100"})
    result = build_stock_by_warehouse_report(
        catalog,
        source="wb",
        wb_api_token="token",
        fetch_wb_raw=lambda _token: _raw_rows(),
    )
    assert [(row.sku, row.name, row.quantity) for row in result.rows] == [
        ("NEW-SKU", MISSING_NAME, 7),
        ("SS100", "NanoGlass", 13),
    ]
    assert result.total_quantity == 20
    assert result.missing_name_count == 1
    assert result.warehouse_title == WB_RF_WAREHOUSE

    book = load_workbook(BytesIO(result.workbook_bytes))
    sheet = book.active
    assert [cell.value for cell in sheet[4]] == ["Артикул", "Название", "Количество"]
    assert [cell.value for cell in sheet[5]] == ["NEW-SKU", MISSING_NAME, 7]
    assert [cell.value for cell in sheet[6]] == ["SS100", "NanoGlass", 13]
    assert sheet[7][0].value == "Итого"
    assert sheet[7][2].value == 20


def test_other_sources_not_implemented(db_url: str) -> None:
    catalog = _catalog(db_url)
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="own")
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="yandex")
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="ozon")
