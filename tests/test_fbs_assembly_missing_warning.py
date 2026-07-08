"""Предупреждение о пропущенных артикулах — исходный регистр из FBS, не casefold."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.fbs_assembly_order import (
    apply_assembly_order_to_ozon_rows,
    parse_assembly_sheet_values,
)
from app.ozon_fbs_labels import OzonFbsListRow


def test_missing_warning_uses_original_fbs_sku_casing() -> None:
    rows = [
        OzonFbsListRow(1, "P1", "SS278", 1, ""),
        OzonFbsListRow(2, "P2", "SS414", 1, ""),
    ]
    assembly_values = [
        ["Номенклатура", "", "Ячейка"],
        ["SS414", "", "A-1"],
    ]

    with patch(
        "app.fbs_assembly_order.load_assembly_entries_from_google_sheet",
        return_value=parse_assembly_sheet_values(assembly_values),
    ):
        _, warnings = apply_assembly_order_to_ozon_rows(
            rows,
            default_stocks_sheet_url="https://docs.google.com/spreadsheets/d/x/edit",
            google_service_account_file="/tmp/creds.json",
            assembly_sheet_name="assembly",
            row_factory=OzonFbsListRow,
        )

    assert len(warnings) == 1
    assert "SS278" in warnings[0]
    assert "ss278" not in warnings[0]
