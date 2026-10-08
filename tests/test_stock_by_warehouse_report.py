"""Отчёт остатков на складах: склады WB, колонка «Склад WB РФ»."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.stock_by_warehouse_report import (
    IN_TRANSIT_FROM_DATE,
    MISSING_NAME,
    WB_RF_WAREHOUSE,
    aggregate_in_transit_quantities,
    build_stock_by_warehouse_report,
    fetch_wb_in_transit_quantities,
    supply_matches_in_transit_cutoff,
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
        fetch_wb_in_transit=lambda _token: {},
    )
    assert [(row.sku, row.name, row.quantity, row.in_transit, row.total) for row in result.rows] == [
        ("NEW-SKU", MISSING_NAME, 7, 0, 7),
        ("SS100", "NanoGlass", 13, 0, 13),
    ]
    assert result.total_quantity == 20
    assert result.total_in_transit == 0
    assert result.total_all == 20
    assert result.missing_name_count == 1
    assert result.warehouse_title == WB_RF_WAREHOUSE

    book = load_workbook(BytesIO(result.workbook_bytes))
    sheet = book.active
    assert [cell.value for cell in sheet[4]] == ["Артикул", "Название", "Количество", "В пути", "Всего"]
    assert [cell.value for cell in sheet[5]] == ["NEW-SKU", MISSING_NAME, 7, 0, 7]
    assert [cell.value for cell in sheet[6]] == ["SS100", "NanoGlass", 13, 0, 13]
    assert sheet[7][0].value == "Итого"
    assert [sheet[7][2].value, sheet[7][3].value, sheet[7][4].value] == [20, 0, 20]


def test_wb_stock_report_adds_in_transit_and_total(db_url: str) -> None:
    catalog = _catalog(db_url)
    catalog.create_product({"name": "NanoGlass", "sku": "SS100", "code": "00100"})
    catalog.create_product({"name": "Case", "sku": "SS400", "code": "00400"})
    result = build_stock_by_warehouse_report(
        catalog,
        source="wb",
        wb_api_token="token",
        fetch_wb_raw=lambda _token: _raw_rows(),
        fetch_wb_in_transit=lambda _token: {"SS100": 5, "ss400": 2},
    )
    assert [(row.sku, row.name, row.quantity, row.in_transit, row.total) for row in result.rows] == [
        ("NEW-SKU", MISSING_NAME, 7, 0, 7),
        ("SS100", "NanoGlass", 13, 5, 18),
        ("SS400", "Case", 0, 2, 2),
    ]
    assert result.total_quantity == 20
    assert result.total_in_transit == 7
    assert result.total_all == 27

    book = load_workbook(BytesIO(result.workbook_bytes))
    sheet = book.active
    assert [cell.value for cell in sheet[5]] == ["NEW-SKU", MISSING_NAME, 7, 0, 7]
    assert [cell.value for cell in sheet[6]] == ["SS100", "NanoGlass", 13, 5, 18]
    assert [cell.value for cell in sheet[7]] == ["SS400", "Case", 0, 2, 2]
    assert [sheet[8][2].value, sheet[8][3].value, sheet[8][4].value] == [20, 7, 27]


def test_aggregate_in_transit_quantities_sums_vendor_codes() -> None:
    assert aggregate_in_transit_quantities(
        [
            {"vendorCode": "SS100", "quantity": 4, "acceptedQuantity": 1},
            {"vendorCode": "ss100", "quantity": 3},
            {"vendorCode": "SS400", "quantity": 2},
            {"vendorCode": "", "quantity": 9},
            {"vendorCode": "SKIP", "quantity": 0},
        ]
    ) == {"SS100": 7, "SS400": 2}


def test_supply_cutoff_keeps_from_september_2026() -> None:
    assert IN_TRANSIT_FROM_DATE.isoformat() == "2026-09-01"
    assert supply_matches_in_transit_cutoff({"createDate": "2026-09-01T00:00:00+03:00"})
    assert supply_matches_in_transit_cutoff({"createDate": "2026-10-08T12:00:00+03:00"})
    assert not supply_matches_in_transit_cutoff({"createDate": "2026-08-31T23:59:59+03:00"})
    assert not supply_matches_in_transit_cutoff({"createDate": "2025-12-01T00:00:00+03:00"})
    assert supply_matches_in_transit_cutoff({"supplyDate": "2026-09-15T00:00:00+03:00"})
    assert not supply_matches_in_transit_cutoff({})


def test_fetch_wb_in_transit_quantities_lists_statuses_and_goods() -> None:
    calls: list[tuple[str, str, dict | None, dict | None]] = []

    class _Resp:
        def __init__(self, payload, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code
            self.ok = status_code < 400
            self.content = b"{}" if payload is not None else b""
            self.text = ""
            self.reason = "OK"
            self.url = "https://supplies-api.wildberries.ru/api/v1/supplies"

        def json(self):
            return self._payload

    def _request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json"), kwargs.get("params")))
        if method == "POST":
            return _Resp(
                [
                    {
                        "supplyID": 11,
                        "preorderID": 1,
                        "statusID": 2,
                        "createDate": "2026-09-01T00:00:00+03:00",
                    },
                    {
                        "supplyID": None,
                        "preorderID": 22,
                        "statusID": 6,
                        "createDate": "2026-10-01T12:00:00+03:00",
                    },
                    {
                        "supplyID": 33,
                        "statusID": 3,
                        "createDate": "2026-09-15T00:00:00+03:00",
                    },
                    {
                        "supplyID": 44,
                        "statusID": 2,
                        "createDate": "2026-08-31T23:00:00+03:00",
                    },
                    {
                        "supplyID": 55,
                        "statusID": 2,
                        "createDate": "2025-01-10T00:00:00+03:00",
                    },
                ]
            )
        if url.endswith("/11/goods"):
            return _Resp([{"vendorCode": "SS100", "quantity": 4}])
        if url.endswith("/22/goods"):
            return _Resp([{"vendorCode": "SS100", "quantity": 1}, {"vendorCode": "SS400", "quantity": 2}])
        if url.endswith("/33/goods"):
            return _Resp([])
        raise AssertionError(url)

    with patch("app.stock_by_warehouse_report.requests.request", side_effect=_request):
        with patch("app.stock_by_warehouse_report.time.sleep"):
            result = fetch_wb_in_transit_quantities("token")

    assert result == {"SS100": 5, "SS400": 2}
    assert calls[0][0] == "POST"
    assert calls[0][2]["statusIDs"] == [2, 3, 6]
    assert calls[0][2]["dates"][0]["type"] == "createDate"
    assert calls[0][2]["dates"][0]["from"] == "2026-09-01"
    till = str(calls[0][2]["dates"][0]["till"])
    assert len(till) == 10 and till[4] == "-" and till[7] == "-"
    assert "T" not in till
    goods_urls = [url for method, url, _body, _params in calls if method == "GET"]
    assert goods_urls == [
        "https://supplies-api.wildberries.ru/api/v1/supplies/11/goods",
        "https://supplies-api.wildberries.ru/api/v1/supplies/22/goods",
        "https://supplies-api.wildberries.ru/api/v1/supplies/33/goods",
    ]
    assert calls[2][3]["isPreorderID"] == "true"


def test_other_sources_not_implemented(db_url: str) -> None:
    catalog = _catalog(db_url)
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="own")
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="yandex")
    with pytest.raises(ValueError, match="пока не реализована"):
        build_stock_by_warehouse_report(catalog, source="ozon")
