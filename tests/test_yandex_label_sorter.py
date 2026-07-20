"""Тесты сортировки ярлыков Яндекс по Google Sheets."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.fbs_labels_common import merge_label_pdfs
from app.yandex_label_sorter import (
    LabelKey,
    extract_label_key_from_page_text,
    extract_sheet_label_keys,
    parse_sheet_label_key,
    sort_yandex_label_pdf,
)


def _pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    y = 180
    for line in text.split("\n"):
        c.drawString(20, y, line)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def _page_texts(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(page.extract_text() or "").replace("\n", " ") for page in reader.pages]


def test_parse_sheet_label_key_with_and_without_fraction() -> None:
    assert parse_sheet_label_key("59153319811 1/2") == LabelKey("59153319811", 1, 2)
    assert parse_sheet_label_key("59153319811") == LabelKey("59153319811", 1, 1)
    assert parse_sheet_label_key("SKU-1") is None


def test_extract_sheet_label_keys_preserves_row_order() -> None:
    rows = [
        ["Артикул", "Кол-во", "Заказ"],
        ["A", "1", "10000101 2/2"],
        ["B", "1", "10000101 1/2"],
        ["C", "1", "10000202"],
        ["D", "1", "10000101 1/2"],  # дубль — пропускаем
    ]
    assert extract_sheet_label_keys(rows) == [
        LabelKey("10000101", 2, 2),
        LabelKey("10000101", 1, 2),
        LabelKey("10000202", 1, 1),
    ]


def test_extract_label_key_from_yandex_page_text() -> None:
    text = "1/2\n59153319811-1\nНомер заказа\n59153319811"
    assert extract_label_key_from_page_text(text) == LabelKey("59153319811", 1, 2)

    text2 = "2/2\n59153319811-2\nНомер заказа\n59153319811"
    assert extract_label_key_from_page_text(text2) == LabelKey("59153319811", 2, 2)

    text3 = "Номер заказа\n59153319811"
    assert extract_label_key_from_page_text(text3) == LabelKey("59153319811", 1, 1)


def test_sort_yandex_label_pdf_orders_by_sheet_and_drops_extras() -> None:
    page_a = _pdf_with_text("1/2\n10000101-1\nНомер заказа\n10000101")
    page_b = _pdf_with_text("2/2\n10000101-2\nНомер заказа\n10000101")
    page_c = _pdf_with_text("Номер заказа\n10000202")
    page_extra = _pdf_with_text("Номер заказа\n10009999")
    # В PDF страницы в «неправильном» порядке + лишняя.
    source = merge_label_pdfs([page_b, page_extra, page_c, page_a])
    assert source is not None

    result = sort_yandex_label_pdf(
        source,
        [
            LabelKey("10000101", 1, 2),
            LabelKey("10000101", 2, 2),
            LabelKey("10000202", 1, 1),
        ],
    )

    texts = _page_texts(result.pdf_bytes)
    assert len(texts) == 3
    assert "10000101-1" in texts[0] or "10000101" in texts[0]
    assert "1/2" in texts[0]
    assert "2/2" in texts[1]
    assert "10000202" in texts[2]
    assert result.stats["matched"] == 3
    assert result.stats["extras_dropped"] == 1
    assert any("10009999" in w for w in result.warnings)


def test_sort_yandex_label_pdf_warns_about_missing_labels() -> None:
    page_a = _pdf_with_text("1/2\n10000101-1\nНомер заказа\n10000101")
    result = sort_yandex_label_pdf(
        page_a,
        [
            LabelKey("10000101", 1, 2),
            LabelKey("10000101", 2, 2),
        ],
    )
    assert result.stats["matched"] == 1
    assert result.stats["missing"] == 1
    assert result.stats["output_pages"] == 1
    assert any("10000101 2/2" in w for w in result.warnings)


def test_sort_yandex_label_pdf_rejects_non_pdf() -> None:
    with pytest.raises(ValueError, match="не является PDF"):
        sort_yandex_label_pdf(b"not-a-pdf", [LabelKey("10000101", 1, 1)])


def test_sort_yandex_label_pdf_requires_at_least_one_match() -> None:
    page = _pdf_with_text("Номер заказа\n99999999")
    with pytest.raises(ValueError, match="Не удалось сопоставить"):
        sort_yandex_label_pdf(page, [LabelKey("10000101", 1, 1)])
