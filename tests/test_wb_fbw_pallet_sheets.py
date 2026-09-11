"""Листы паллет FBW по примеру «поставка лист.docx»."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.wb_fbw_pallet_sheets import (
    WbFbwPalletSheetData,
    fbw_sheet_city,
    generate_wb_fbw_pallet_sheets_pdf,
    parse_pallet_count,
)
from app.wb_fbw_supply_qr import (
    extract_wb_supply_qr_code_from_pdf,
    normalize_wb_supply_qr_code,
)


def test_fbw_sheet_city() -> None:
    assert fbw_sheet_city("СЦ Новосибирск 4") == "Новосибирск"
    assert fbw_sheet_city("Коледино") == "Коледино"
    assert fbw_sheet_city("Казань") == "Казань"
    assert fbw_sheet_city("") == ""


def test_pallet_sheets_pdf_matches_example() -> None:
    pdf = generate_wb_fbw_pallet_sheets_pdf(
        WbFbwPalletSheetData(supply_id="41349716", city="Новосибирск", pallet_count=4)
    )
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 4
    page = reader.pages[0]
    assert round(float(page.mediabox.width), 0) == 842
    assert round(float(page.mediabox.height), 0) == 595
    texts = [(p.extract_text() or "") for p in reader.pages]
    assert "WILDBERRIES" in texts[0]
    assert "Поставка № 41349716" in texts[0]
    assert "Новосибирск" in texts[0]
    assert "Палет 1 из 4" in texts[0]
    assert "Палет 2 из 4" in texts[1]
    assert "Палет 4 из 4" in texts[3]


def test_parse_pallet_count_rejects_zero() -> None:
    try:
        parse_pallet_count(0)
    except ValueError as exc:
        assert "не меньше 1" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_normalize_supply_qr_code() -> None:
    assert normalize_wb_supply_qr_code("WB-GI-27768 9956") == "WB-GI-277689956"
    assert normalize_wb_supply_qr_code("WB-GI-277689956") == "WB-GI-277689956"
    assert normalize_wb_supply_qr_code("41357389") == ""


def test_extract_gi_from_generated_pdf() -> None:
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 80))
    c.setFont("Helvetica", 10)
    c.drawString(10, 40, "WB-GI-27768 9956")
    c.save()
    assert extract_wb_supply_qr_code_from_pdf(buf.getvalue()) == "WB-GI-277689956"
