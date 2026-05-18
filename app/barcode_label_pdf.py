"""PDF-этикетка со штрихкодом Code 128 (скачивание, без печати)."""

from __future__ import annotations

import io

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts

# Термоэтикетка 40×30 мм
LABEL_WIDTH_MM = 40.0
LABEL_HEIGHT_MM = 30.0
BARCODE_SIDE_MARGIN_MM = 0.5
TEXT_SIDE_MARGIN_MM = 1.2
NAME_MAX_LINES = 3


def _render_code128_image(barcode_value: str, *, module_width: float) -> Image.Image:
    """Полосы Code 128; module_width подбирается под ширину этикетки."""
    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": module_width,
            "module_height": 10.0,
            "quiet_zone": 0.5,
            "font_size": 0,
            "text_distance": 0,
        }
    )
    code = Code128(barcode_value, writer=writer)
    buf = io.BytesIO()
    code.write(buf, options={"write_text": False})
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _truncate_to_width(text: str, font_name: str, font_size: float, max_width: float) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if pdfmetrics.stringWidth(s, font_name, font_size) <= max_width:
        return s
    ell = "…"
    while len(s) > 1 and pdfmetrics.stringWidth(s + ell, font_name, font_size) > max_width:
        s = s[:-1]
    return s + ell


def _wrap_name_lines(
    name: str,
    font_name: str,
    font_size: float,
    max_width: float,
    *,
    max_lines: int = NAME_MAX_LINES,
) -> list[str]:
    """До max_lines строк; длинные слова режутся по символам."""
    s = " ".join(str(name or "").split())
    if not s:
        return []

    def fits(chunk: str) -> bool:
        return pdfmetrics.stringWidth(chunk, font_name, font_size) <= max_width

    lines: list[str] = []
    words = s.split(" ")
    idx = 0
    while idx < len(words) and len(lines) < max_lines:
        word = words[idx]
        if not fits(word):
            chunk = ""
            for ch in word:
                trial = chunk + ch
                if fits(trial):
                    chunk = trial
                else:
                    if chunk:
                        lines.append(chunk)
                        chunk = ch
                        if len(lines) >= max_lines:
                            break
                    else:
                        lines.append(ch)
                        if len(lines) >= max_lines:
                            break
                        chunk = ""
            if chunk and len(lines) < max_lines:
                lines.append(chunk)
            idx += 1
            continue

        current = word
        idx += 1
        while idx < len(words):
            trial = f"{current} {words[idx]}"
            if fits(trial):
                current = trial
                idx += 1
            else:
                break
        lines.append(current)

    if idx < len(words) and lines:
        rest = " ".join(words[idx:])
        lines[-1] = _truncate_to_width(f"{lines[-1]} {rest}", font_name, font_size, max_width)
    return lines[:max_lines]


def _fit_barcode_dims(
    barcode_value: str,
    max_w_pt: float,
    max_h_pt: float,
) -> tuple[Image.Image, float, float]:
    """Штрихкод на всю доступную ширину; module_width увеличиваем, пока влезает по высоте."""
    best: tuple[Image.Image, float, float] | None = None
    for mw_int in range(15, 70, 1):
        mw = mw_int / 100.0
        img = _render_code128_image(barcode_value, module_width=mw)
        if img.width <= 0 or img.height <= 0:
            continue
        draw_w = max_w_pt
        draw_h = draw_w * img.height / img.width
        if draw_h <= max_h_pt:
            best = (img, draw_w, draw_h)
        else:
            break
    if best is not None:
        return best
    img = _render_code128_image(barcode_value, module_width=0.15)
    draw_h = min(max_h_pt, max_w_pt * img.height / img.width)
    draw_w = draw_h * img.width / img.height
    return img, draw_w, draw_h


def _draw_centered_lines(
    c: canvas.Canvas,
    lines: list[str],
    *,
    cx: float,
    y_top: float,
    font_name: str,
    font_size: float,
    line_step: float,
) -> float:
    """Рисует строки сверху вниз; возвращает занятую высоту (pt)."""
    if not lines:
        return 0.0
    c.setFont(font_name, font_size)
    for i, line in enumerate(lines):
        c.drawCentredString(cx, y_top - i * line_step, line)
    return (len(lines) - 1) * line_step + font_size * 0.85


def generate_barcode_label_pdf(
    barcode_value: str,
    *,
    sku: str = "",
    product_name: str = "",
    width_mm: float = LABEL_WIDTH_MM,
    height_mm: float = LABEL_HEIGHT_MM,
) -> bytes:
    """Этикетка 40×30 мм: название (до 3 строк), штрихкод, ШК, артикул."""
    value = str(barcode_value or "").strip()
    if not value:
        raise ValueError("Пустое значение штрихкода")

    sku_s = str(sku or "").strip()
    name_s = str(product_name or "").strip()

    page_w = width_mm * mm
    page_h = height_mm * mm
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))

    margin_v = 1.0 * mm
    text_w = page_w - 2 * TEXT_SIDE_MARGIN_MM * mm
    barcode_w = page_w - 2 * BARCODE_SIDE_MARGIN_MM * mm
    cx = page_w / 2

    font, font_bold = get_pdf_label_fonts()
    name_pt = 5.8
    line_pt = 5.8
    name_line_step = 2.15 * mm
    footer_line_step = 2.2 * mm

    # Низ: артикул и текст ШК
    c.setFont(font, line_pt)
    y_art = margin_v
    y_bc = margin_v + footer_line_step
    footer_top = y_bc + line_pt * 0.9
    if sku_s:
        c.drawCentredString(cx, y_art, _truncate_to_width(f"Арт. {sku_s}", font, line_pt, text_w))
    c.drawCentredString(cx, y_bc, _truncate_to_width(f"ШК {value}", font, line_pt, text_w))

    zone_bottom = footer_top + 0.6 * mm
    zone_top = page_h - margin_v

    # Верх: название (до 3 строк)
    if name_s:
        name_lines = _wrap_name_lines(name_s, font_bold, name_pt, text_w, max_lines=NAME_MAX_LINES)
        name_block = _draw_centered_lines(
            c,
            name_lines,
            cx=cx,
            y_top=zone_top - name_pt * 0.2,
            font_name=font_bold,
            font_size=name_pt,
            line_step=name_line_step,
        )
        zone_top = zone_top - name_block - 0.5 * mm

    # Центр: штрихкод на всю ширину (полосы, не текст)
    max_h = zone_top - zone_bottom
    if max_h < 2.5 * mm:
        max_h = 2.5 * mm
    img, draw_w, draw_h = _fit_barcode_dims(value, barcode_w, max_h)
    x = (page_w - draw_w) / 2
    img_y = zone_bottom + (max_h - draw_h) / 2
    c.drawImage(ImageReader(img), x, img_y, width=draw_w, height=draw_h, preserveAspectRatio=False, mask="auto")

    c.showPage()
    c.save()
    return pdf_buf.getvalue()
