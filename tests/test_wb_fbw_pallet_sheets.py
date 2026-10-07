"""Листы паллет FBW по примеру «поставка лист.docx»."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts
from app.wb_fbo_sheet_packing_sheets import (
    WbFboPackingSheetData,
    display_legal_name,
    generate_wb_fbo_packing_sheets_pdf,
)
from app.wb_fbw_pallet_sheets import (
    WbFbwPalletSheetData,
    fbw_sheet_city,
    generate_wb_fbw_pallet_sheets_pdf,
    parse_pallet_count,
)
from app.wb_fbw_supply_qr import (
    extract_wb_supply_data_from_pdf,
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


def test_extract_supply_data_from_qr_pdf() -> None:
    regular, _bold = get_pdf_label_fonts()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(500, 700))
    c.setFont(regular, 12)
    lines = [
        "Тип поставки:",
        "Монопаллета",
        "9 шт паллет",
        "WB-GI-28734 1284",
        "№ поставки: 41505518",
        "Плановая дата: 11.10.26",
        "Пункт отгрузки: СЦ Коледино",
        "2",
        'Продавец: ООО "ШАЙН',
        'СИСТЕМС"',
    ]
    for index, line in enumerate(lines):
        c.drawString(20, 650 - index * 30, line)
    c.save()

    data = extract_wb_supply_data_from_pdf(buf.getvalue())
    assert data.qr_code == "WB-GI-287341284"
    assert data.supply_id == "41505518"
    assert data.warehouse_name == "СЦ Коледино 2"
    assert data.seller_name == 'ООО "ШАЙН СИСТЕМС"'
    assert data.plan_date == "11.10.2026"
    assert data.supply_type == "Монопаллета"
    assert data.pallet_count == 9


def test_fbo_new_packing_sheets_match_example_layout() -> None:
    pdf = generate_wb_fbo_packing_sheets_pdf(
        WbFboPackingSheetData(
            supply_id="41505518",
            warehouse_name="СЦ Коледино 2",
            seller_name='ООО "ШАЙН СИСТЕМС"',
            plan_date="11.10.2026",
            supply_type="Монопаллета",
            print_count=3,
            pallet_total=9,
        )
    )
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 3
    first = reader.pages[0]
    assert round(float(first.mediabox.width), 0) == 595
    assert round(float(first.mediabox.height), 0) == 842
    text = first.extract_text() or ""
    assert "WILDBERRIES" in text
    assert "Упаковочный лист" in text
    assert "Номер паллеты –" in text and "1" in text
    assert "Количество паллет в поставке –" in text and "9" in text
    assert "Номер поставки –" in text and "41505518" in text
    assert "Склад назначения –" in text and "СЦ Коледино 2" in text
    assert "Тип упаковки –" in text and "Монопаллет" in text
    assert "ООО" in text
    assert "«Шайн Системс»" in text
    assert "11.10.2026 г." in text


def test_display_legal_name_from_qr() -> None:
    assert display_legal_name('ООО "ШАЙН СИСТЕМС"') == "ООО «Шайн Системс»"
    assert display_legal_name("ООО «Шайн Системс»") == "ООО «Шайн Системс»"


def test_extract_real_wb_fbo_new_supply_qr() -> None:
    path = Path(__file__).resolve().parents[1] / "tables_examples" / "8668ac57-4341-4591-a2d9-eaeb4177ef2c.pdf"
    if not path.is_file():
        return
    data = extract_wb_supply_data_from_pdf(path.read_bytes())
    assert data.supply_id == "41505601"
    assert data.warehouse_name == "СЦ Коледино 2"
    assert data.seller_name == 'ООО "ШАЙН СИСТЕМС"'
    assert data.plan_date == "11.10.2026"
    assert data.supply_type == "Монопаллета"
    assert data.pallet_count == 2
    pdf = generate_wb_fbo_packing_sheets_pdf(
        WbFboPackingSheetData(
            supply_id=data.supply_id,
            warehouse_name=data.warehouse_name,
            seller_name=data.seller_name,
            plan_date=data.plan_date,
            supply_type=data.supply_type,
            print_count=data.pallet_count,
            pallet_total=data.pallet_count,
        )
    )
    text = "\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(pdf)).pages)
    assert "Номер поставки –" in text and "41505601" in text
    assert "СЦ Коледино 2" in text
    assert "Монопаллет" in text
    assert "«Шайн Системс»" in text
    assert "11.10.2026 г." in text
    assert "Количество паллет в поставке –" in text and "2" in text
