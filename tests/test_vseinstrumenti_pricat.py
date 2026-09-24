from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.vseinstrumenti_pricat import build_vseinstrumenti_pricat


def _stock() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["A-1", "Первый", 20, 3, 17])
    sheet.append(["A-2", "Второй", 10, 2, 8])
    sheet.append(["NO-PRICAT", "Нет в PRICAT", 5, 0, 5])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _pricat() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Лист 1"
    sheet["C6"] = "Актуальные остатки 29.06"
    sheet["E12"] = "Артикул"
    sheet["AB12"] = "Остаток на складе"
    sheet["E13"] = "A-1"
    sheet["AB13"] = 999
    sheet["E14"] = "A-2"
    sheet["AB14"] = 999
    sheet["E15"] = "ONLY-PRICAT"
    sheet["AB15"] = 777
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_build_vseinstrumenti_pricat() -> None:
    result, stats = build_vseinstrumenti_pricat(
        _stock(),
        _pricat(),
        actual_date=date(2026, 9, 24),
    )
    sheet = load_workbook(BytesIO(result), data_only=True)["Лист 1"]

    assert sheet["AB13"].value == 5
    assert sheet["AB14"].value == 2
    assert sheet["AB15"].value == 777
    assert sheet["C6"].value == "Актуальные остатки 24.09"
    assert stats == {"source_articles": 3, "updated": 2, "not_found_in_pricat": 1}


def test_bundled_pricat_template_is_available() -> None:
    result, stats = build_vseinstrumenti_pricat(_stock(), actual_date=date(2026, 9, 24))
    sheet = load_workbook(BytesIO(result), data_only=True)["Лист 1"]

    assert sheet["C6"].value == "Актуальные остатки 24.09"
    assert stats["source_articles"] == 3
