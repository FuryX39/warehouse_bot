"""Тесты расчёта грузомест и сохранения ОВХ/веса."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.cargo_place_calculator import calculate_cargo_places, parse_cargo_place_workbook
from app.catalog_repository import CatalogProduct, CatalogRepository


def _workbook(rows: list[tuple[object, object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Артикул", "Количество"])
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class _FakeCatalog:
    def __init__(self) -> None:
        self.products = {
            "sku-1": {
                "id": 1,
                "sku": "SKU-1",
                "name": "Товар 1",
                "length_mm": "1000",
                "width_mm": "500",
                "height_mm": "400",
                "weight_kg": "2.5",
            }
        }

    def list_cargo_place_types(self):
        return [
            {
                "id": 10,
                "name": "Короб",
                "length_mm": "1000",
                "width_mm": "1000",
                "height_mm": "500",
                "volume_liters": "500",
                "comment": "",
            }
        ]

    def lookup_products_with_metrics_by_skus(self, skus):
        return {key: value for key, value in self.products.items() if key in {s.casefold() for s in skus}}


def test_parser_aggregates_duplicate_skus() -> None:
    rows = parse_cargo_place_workbook(_workbook([("SKU-1", 2), ("sku-1", 3)]))
    assert rows == [{"sku": "SKU-1", "quantity": 5, "source_row": 2}]


def test_parser_rejects_non_positive_or_fractional_quantity() -> None:
    with pytest.raises(ValueError, match="целым и больше нуля"):
        parse_cargo_place_workbook(_workbook([("SKU-1", 1.5)]))
    with pytest.raises(ValueError, match="целым и больше нуля"):
        parse_cargo_place_workbook(_workbook([("SKU-1", 0)]))


def test_calculation_sums_liters_weight_and_rounds_places_up() -> None:
    result = calculate_cargo_places(
        _workbook([("SKU-1", 3)]),
        catalog_repo=_FakeCatalog(),
        cargo_place_type_id=10,
    )
    # 1000 × 500 × 400 мм = 200 л; 3 единицы = 600 л.
    assert result["total_volume_liters"] == 600.0
    assert result["total_weight_kg"] == 7.5
    assert result["cargo_place_count"] == 2
    assert result["volume_complete"] is True
    assert result["weight_complete"] is True


def test_unknown_sku_is_reported_and_blocks_place_count() -> None:
    result = calculate_cargo_places(
        _workbook([("UNKNOWN", 2)]),
        catalog_repo=_FakeCatalog(),
        cargo_place_type_id=10,
    )
    assert result["cargo_place_count"] is None
    assert result["rows"][0]["missing_fields"] == ["product"]
    assert "не найден" in result["rows"][0]["error"]


def test_missing_dimensions_are_reported_for_inline_editing() -> None:
    catalog = _FakeCatalog()
    catalog.products["sku-1"]["width_mm"] = ""
    result = calculate_cargo_places(
        _workbook([("SKU-1", 1)]),
        catalog_repo=catalog,
        cargo_place_type_id=10,
    )
    assert result["cargo_place_count"] is None
    assert result["weight_complete"] is True
    assert result["rows"][0]["missing_fields"] == ["width_mm"]
    assert result["rows"][0]["total_weight_kg"] == 2.5


def test_repository_saves_cargo_types_and_product_metrics(tmp_path) -> None:
    repo = CatalogRepository(f"sqlite:///{tmp_path / 'catalog.sqlite3'}")
    repo.init_schema()
    types = repo.save_cargo_place_types(
        [
            {
                "name": "Палета",
                "length_mm": "1200",
                "width_mm": "800",
                "height_mm": "1500",
                "comment": "Европалета",
            }
        ]
    )
    assert types[0]["volume_liters"] == "1440"

    with Session(repo.engine) as session:
        product = CatalogProduct(
            name="Товар",
            sku="SKU-X",
            code="CODE-X",
            is_kit=False,
            created_at_ts=1,
            updated_at_ts=1,
        )
        session.add(product)
        session.commit()
        product_id = int(product.id)
    saved = repo.update_product_metrics(
        product_id,
        length_mm="100",
        width_mm="200",
        height_mm="300",
        weight_kg="1,25",
    )
    assert saved["volume_liters"] == "6"
    assert saved["weight_kg"] == "1.25"
    indexed = repo.lookup_products_with_metrics_by_skus(["sku-x"])
    assert indexed["sku-x"]["length_mm"] == "100"
