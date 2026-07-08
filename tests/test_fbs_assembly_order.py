"""Тесты порядка FBS-списка по листу assembly (формат tables_examples/list.xlsx)."""

from __future__ import annotations

from types import SimpleNamespace

from app.fbs_assembly_order import (
    extract_offer_id_from_cell,
    parse_assembly_sheet_values,
    reorder_ozon_fbs_list_rows,
    sku_match_key,
)
from app.ozon_fbs_labels import OzonFbsListRow


def _sample_assembly_values() -> list[list[str]]:
    """Минимальный фрагмент листа ТСД: колонки A (артикул), C (ячейка), E (кол-во)."""
    return [
        ["Номенклатура", "", "Ячейка", "", "Количество"],
        ["SSBL997", "", "A-01", "", "1"],
        ["SS414", "", "B-02", "", "2"],
        ["SS996", "", "C-03", "", "1"],
    ]


def test_parse_assembly_sheet_values_skips_header_and_reads_columns() -> None:
    entries = parse_assembly_sheet_values(_sample_assembly_values())
    assert len(entries) == 3
    assert entries[0].sku == "SSBL997"
    assert entries[0].place == "A-01"
    assert entries[0].sort_index == 0
    assert entries[2].sku == "SS996"


def test_parse_prefers_artikul_column_over_nomenclature() -> None:
    values = [
        ["Номенклатура", "Артикул", "Ячейка"],
        ["Подушка декоративная", "SS278", "A-01"],
    ]
    entries = parse_assembly_sheet_values(values)
    assert len(entries) == 1
    assert entries[0].sku == "SS278"


def test_parse_extracts_offer_id_from_product_name() -> None:
    values = [
        ["Номенклатура", "", "Ячейка"],
        ["Подушка SS278 синяя", "", "B-02"],
    ]
    entries = parse_assembly_sheet_values(values)
    assert len(entries) == 1
    assert entries[0].sku == "SS278"


def test_extract_offer_id_from_cell() -> None:
    assert extract_offer_id_from_cell("SS533") == "SS533"
    assert extract_offer_id_from_cell("Товар SS540 красный") == "SS540"


def test_reorder_follows_assembly_walk_top_to_bottom() -> None:
    entries = parse_assembly_sheet_values(_sample_assembly_values())
    rows = [
        SimpleNamespace(posting_number="P1", sku="SS996", quantity=1, status=""),
        SimpleNamespace(posting_number="P2", sku="SS414", quantity=2, status=""),
        SimpleNamespace(posting_number="P3", sku="SSBL997", quantity=1, status=""),
    ]
    out = reorder_ozon_fbs_list_rows(rows, entries, row_factory=OzonFbsListRow)
    assert [(r.sku, r.posting_number) for r in out] == [
        ("SSBL997", "P3"),
        ("SS414", "P2"),
        ("SS996", "P1"),
    ]


def test_reorder_duplicate_assembly_rows_consume_fbs_lines_in_order() -> None:
    entries = parse_assembly_sheet_values(
        [
            ["Номенклатура", "", "Ячейка"],
            ["SS278", "", "A-1"],
            ["SS278", "", "A-2"],
            ["SS533", "", "B-1"],
        ]
    )
    rows = [
        SimpleNamespace(posting_number="P1", sku="SS278", quantity=1, status=""),
        SimpleNamespace(posting_number="P2", sku="SS278", quantity=1, status=""),
        SimpleNamespace(posting_number="P3", sku="SS533", quantity=1, status=""),
    ]
    out = reorder_ozon_fbs_list_rows(rows, entries, row_factory=OzonFbsListRow)
    assert [(r.sku, r.posting_number) for r in out] == [
        ("SS278", "P1"),
        ("SS278", "P2"),
        ("SS533", "P3"),
    ]


def test_sku_match_key_case_insensitive() -> None:
    assert sku_match_key("SS278") == sku_match_key("ss278")
    assert sku_match_key(" SS278 ") == sku_match_key("SS278")
    assert sku_match_key("'SS278") == sku_match_key("SS278")
