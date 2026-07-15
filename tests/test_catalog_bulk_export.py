from io import BytesIO

from openpyxl import load_workbook

from app.catalog_bulk_import import build_products_export


class _ExportRepo:
    def __init__(self) -> None:
        self.filters = None

    def list_products_for_export(self, filters):
        self.filters = filters
        return {
            "price_types": [{"id": 7, "name": "Розница"}],
            "products": [
                {
                    "name": "Набор",
                    "sku": "KIT-1",
                    "code": "001",
                    "external_code": "EXT",
                    "description": "Описание",
                    "image_url": "https://example.test/image.jpg",
                    "group_name": "Наборы",
                    "country": "Россия",
                    "unit_name": "шт",
                    "weight": "1",
                    "width_mm": "100",
                    "height_mm": "50",
                    "length_mm": "20",
                    "volume": "0.1",
                    "marking_type_name": "Не подлежит маркировке",
                    "barcodes": [{"barcode": "123"}, {"barcode": "456"}],
                    "is_kit": True,
                    "components": [{"sku": "ITEM-1", "name": "Товар", "quantity": 2}],
                    "prices": {7: "999.90"},
                }
            ],
        }


def test_products_export_contains_catalog_details_and_prices():
    repo = _ExportRepo()

    content = build_products_export(repo, {"group_id": "3"})

    assert repo.filters == {"group_id": "3"}
    workbook = load_workbook(BytesIO(content), data_only=True)
    sheet = workbook["Товары"]
    headers = [cell.value for cell in sheet[1]]
    row = [cell.value for cell in sheet[2]]

    assert headers[:3] == ["Название*", "Артикул*", "Код*"]
    assert headers[-3:] == ["Тип", "Состав комплекта", "Цена: Розница"]
    assert row[15] == "123; 456"
    assert row[16] == "Комплект"
    assert row[17] == "ITEM-1 × 2"
    assert row[18] == "999.90"
