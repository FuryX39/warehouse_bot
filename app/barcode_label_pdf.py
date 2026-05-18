"""PDF-этикетка со штрихкодом Code 128 (скачивание, без печати)."""

from __future__ import annotations

import io

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts

# Термоэтикетка 40×30 мм
LABEL_WIDTH_MM = 40.0
LABEL_HEIGHT_MM = 30.0
# Боковые поля только для полос штрихкода (текст — с чуть большим отступом)
BARCODE_SIDE_MARGIN_MM = 0.6
TEXT_SIDE_MARGIN_MM = 1.2


def _render_code128_image(barcode_value: str) -> Image.Image:
    """Полосы Code 128 без подписи — цифры ШК рисуем отдельно на этикетке."""
    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": 0.2,
            "module_height": 9.0,
            "quiet_zone": 0.6,
            "font_size": 0,
            "text_distance": 0,
        }
    )
    code = Code128(barcode_value, writer=writer)
    buf = io.BytesIO()
    code.write(buf, options={"write_text": False})
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _truncate_to_width(c: canvas.Canvas, text: str, font_name: str, font_size: float, max_width: float) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if c.stringWidth(s, font_name, font_size) <= max_width:
        return s
    ell = "…"
    while len(s) > 1 and c.stringWidth(s + ell, font_name, font_size) > max_width:
        s = s[:-1]
    return s + ell


def generate_barcode_label_pdf(
    barcode_value: str,
    *,
    sku: str = "",
    product_name: str = "",
    width_mm: float = LABEL_WIDTH_MM,
    height_mm: float = LABEL_HEIGHT_MM,
) -> bytes:
    """Этикетка 40×30 мм: название, штрихкод, ШК, артикул."""
    value = str(barcode_value or "").strip()
    if not value:
        raise ValueError("Пустое значение штрихкода")

    sku_s = str(sku or "").strip()
    name_s = str(product_name or "").strip()

    page_w = width_mm * mm
    page_h = height_mm * mm
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))

    margin_v = 1.2 * mm
    text_w = page_w - 2 * TEXT_SIDE_MARGIN_MM * mm
    barcode_w = page_w - 2 * BARCODE_SIDE_MARGIN_MM * mm
    cx = page_w / 2

    font, font_bold = get_pdf_label_fonts()
    name_pt = 6.5
    line_pt = 6.0
    line_step = 2.4 * mm
    name_block = 4.0 * mm
    footer_block = 2 * line_step + 0.8 * mm

    # Низ: артикул и ШК
    c.setFont(font, line_pt)
    y_art = margin_v
    y_bc = margin_v + line_step
    if sku_s:
        c.drawCentredString(cx, y_art, _truncate_to_width(c, f"Арт. {sku_s}", font, line_pt, text_w))
    c.drawCentredString(cx, y_bc, _truncate_to_width(c, f"ШК {value}", font, line_pt, text_w))

    # Верх: название
    zone_bottom = margin_v + footer_block
    zone_top = page_h - margin_v
    if name_s:
        c.setFont(font_bold, name_pt)
        y_name = zone_top - name_pt * 0.35
        c.drawCentredString(cx, y_name, _truncate_to_width(c, name_s, font_bold, name_pt, text_w))
        zone_top = zone_top - name_block

    # Центр: штрихкод на почти всю ширину этикетки
    img = _render_code128_image(value)
    img_w_px, img_h_px = img.size
    max_w = barcode_w
    max_h = zone_top - zone_bottom
    if max_h < 3 * mm:
        max_h = 3 * mm
    scale_w = max_w / img_w_px
    scale_h = max_h / img_h_px
    scale = scale_w
    if img_h_px * scale > max_h:
        scale = scale_h
    draw_w = img_w_px * scale
    draw_h = img_h_px * scale
    x = (page_w - draw_w) / 2
    img_y = zone_bottom + (max_h - draw_h) / 2
    c.drawImage(ImageReader(img), x, img_y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

    c.showPage()
    c.save()
    return pdf_buf.getvalue()
