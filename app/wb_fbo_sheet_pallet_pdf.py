"""Этикетки внутренних ШК паллет FBO WB new, 58×40 мм."""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts
from app.wb_fbw_box_label_pdf import LABEL_HEIGHT_MM, LABEL_WIDTH_MM, _draw_qr, _text_y

_LEFT = 5.67


def generate_wb_fbo_sheet_pallet_labels_pdf(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        raise ValueError("Нет паллет для этикеток")
    page_w = LABEL_WIDTH_MM * mm
    page_h = LABEL_HEIGHT_MM * mm
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))
    font, font_bold = get_pdf_label_fonts()
    for row in rows:
        _draw_pallet_label(c, row, page_w=page_w, page_h=page_h, font=font, font_bold=font_bold)
        c.showPage()
    c.save()
    return pdf_buf.getvalue()


def _draw_pallet_label(
    c: canvas.Canvas,
    row: dict[str, Any],
    *,
    page_w: float,
    page_h: float,
    font: str,
    font_bold: str,
) -> None:
    code = str(row.get("pallet_id") or row.get("pallet_human_id") or "").strip()
    if not code:
        raise ValueError("У паллета нет штрихкода")
    seq = int(row.get("seq") or 0)
    title = f"Паллет {seq}" if seq > 0 else "Паллет"
    y_title = _text_y(page_h, top=5.0, size=11.0)
    c.setFont(font_bold, 11.0)
    c.drawString(_LEFT, y_title, title)

    qr_size = 48.0
    _draw_qr(c, code, _LEFT, 18.0, qr_size)

    x = _LEFT + qr_size + 8.0
    c.setFont(font, 6.0)
    c.drawString(x, _text_y(page_h, 18.0, 6.0), "ШК паллета:")
    c.setFont(font_bold, 7.0)
    max_w = page_w - x - 6.0
    shown = code
    while shown and pdfmetrics.stringWidth(shown, font_bold, 7.0) > max_w:
        shown = shown[:-1]
    c.drawString(x, _text_y(page_h, 28.0, 7.0), shown)

    supply = str(row.get("supply_id") or "").strip()
    if supply:
        c.setFont(font, 6.0)
        c.drawString(x, _text_y(page_h, 42.0, 6.0), "Поставка:")
        c.setFont(font_bold, 7.0)
        c.drawString(x, _text_y(page_h, 52.0, 7.0), supply)
