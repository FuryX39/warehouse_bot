"""Тесты преобразования Excel в PDF."""

from __future__ import annotations

import io
from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfReader

from app.excel_to_pdf import excel_to_pdf, excel_to_pdf_download_name, list_excel_to_pdf_profiles

_TABLES_DIR = Path(__file__).resolve().parents[1] / "tables_examples"


def _make_simple_xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_list_profiles_contains_free_and_vseinstrumenti() -> None:
    profiles = list_excel_to_pdf_profiles()
    ids = {item["id"] for item in profiles}
    assert ids == {"free", "vseinstrumenti"}


def test_free_profile_builds_multipage_pdf() -> None:
    rows = [["Колонка A", "Колонка B", "Колонка C"]]
    rows += [[f"R{i}", f"Значение {i}", f"Длинный текст строки {i}"] for i in range(1, 80)]
    pdf = excel_to_pdf(_make_simple_xlsx(rows), profile="free", original_filename="table.xlsx")
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 2


def test_vseinstrumenti_profile_extracts_row_18_and_columns() -> None:
    sample = next(
        (
            path
            for path in _TABLES_DIR.glob("*.xlsx")
            if not path.name.startswith("~$") and "4154590" in path.name
        ),
        None,
    )
    if sample is None:
        return
    pdf = excel_to_pdf(sample.read_bytes(), profile="vseinstrumenti", original_filename=sample.name)
    assert pdf.startswith(b"%PDF")
    text = (PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "").casefold()
    assert "ss785" in text or "штрих" in text


def test_vseinstrumenti_profile_on_synthetic_workbook() -> None:
    rows: list[list[object]] = [[""] for _ in range(17)]
    rows.append(["№", "Штрихкод", "Наименование", "Код ВИ", "", "", "Количество"])
    rows.append([1, "4652245476537", "Товар 1", "SS785", "", "", 4])
    rows.append([2, "4673746970256", "Товар 2", "SS913", "", "", 2])
    pdf = excel_to_pdf(_make_simple_xlsx(rows), profile="vseinstrumenti", original_filename="order.xlsx")
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
    assert "SS785" in text
    assert "SS913" in text
    assert "Штрихкод" in text or "штрих" in text.lower()


def test_excel_to_pdf_download_name() -> None:
    assert excel_to_pdf_download_name("Черновик.xlsx", "free") == "Черновик.pdf"
