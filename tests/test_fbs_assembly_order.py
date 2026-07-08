"""Тесты порядка FBS-списка по листу assembly (формат tables_examples/list.xlsx)."""

from __future__ import annotations

from types import SimpleNamespace

from app.fbs_assembly_order import (
    parse_assembly_sheet_values,
    reorder_ozon_fbs_list_rows,
    sku_match_key,
    sku_sort_rank_from_assembly,
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


def test_sku_sort_rank_first_occurrence_wins() -> None:
    values = _sample_assembly_values() + [["SS414", "", "Z-99", "", "1"]]
    rank = sku_sort_rank_from_assembly(parse_assembly_sheet_values(values))
    assert rank["ssbl997"] == 0
    assert rank["ss414"] == 1
    assert rank["ss996"] == 2


def test_reorder_ozon_fbs_list_rows_by_assembly_route() -> None:
    rank = sku_sort_rank_from_assembly(parse_assembly_sheet_values(_sample_assembly_values()))
    rows = [
        SimpleNamespace(posting_number="P1", sku="SS996", quantity=1, status=""),
        SimpleNamespace(posting_number="P2", sku="SS414", quantity=2, status=""),
        SimpleNamespace(posting_number="P3", sku="SSBL997", quantity=1, status=""),
    ]
    out = reorder_ozon_fbs_list_rows(rows, rank, row_factory=OzonFbsListRow)
    assert [(r.sku, r.posting_number) for r in out] == [
        ("SSBL997", "P3"),
        ("SS414", "P2"),
        ("SS996", "P1"),
    ]
    assert [r.seq for r in out] == [1, 2, 3]


def test_sku_match_key_case_insensitive() -> None:
    assert sku_match_key("SS278") == sku_match_key("ss278")
    assert sku_match_key(" SS278 ") == sku_match_key("SS278")
    assert sku_match_key("'SS278") == sku_match_key("SS278")


def test_reorder_keeps_posting_lines_together() -> None:
    rank = sku_sort_rank_from_assembly(parse_assembly_sheet_values(_sample_assembly_values()))
    rows = [
        SimpleNamespace(posting_number="P1", sku="SS996", quantity=1, status=""),
        SimpleNamespace(posting_number="P1", sku="SS414", quantity=1, status=""),
        SimpleNamespace(posting_number="P2", sku="SSBL997", quantity=1, status=""),
    ]
    out = reorder_ozon_fbs_list_rows(rows, rank, row_factory=OzonFbsListRow)
    assert out[0].posting_number == "P2"
    assert out[0].sku == "SSBL997"
    assert out[1].posting_number == "P1"
    assert {r.sku for r in out[1:3]} == {"SS414", "SS996"}
