"""Листы паллет FBW — как tables_examples/поставка лист.docx."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts

# Word: pgSz landscape A4, sz=144 half-points → 72 pt, по центру.
_FONT_SIZE = 72
_LEADING = 84
_MAX_PALLETS = 200
_WAREHOUSE_PREFIX = re.compile(
    r"^(?:СЦ|РЦ|СКЛАД|складской\s+центр)\s+",
    re.IGNORECASE,
)
_TRAILING_NUM = re.compile(r"\s+\d+$")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class WbFbwPalletSheetData:
    supply_id: str
    city: str
    pallet_count: int


def fbw_sheet_city(warehouse_name: object) -> str:
    """«СЦ Новосибирск 4» → «Новосибирск», как в примере листа."""
    text = str(warehouse_name or "").strip()
    if not text:
        return ""
    stripped = _WAREHOUSE_PREFIX.sub("", text).strip()
    stripped = _TRAILING_NUM.sub("", stripped).strip()
    return stripped or text


def parse_pallet_count(raw: object) -> int:
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Количество паллет должно быть числом") from exc
    if count < 1:
        raise ValueError("Количество паллет должно быть не меньше 1")
    if count > _MAX_PALLETS:
        raise ValueError(f"Количество паллет слишком большое (макс. {_MAX_PALLETS})")
    return count


def pallet_sheets_filename(supply_id: object) -> str:
    number = str(supply_id or "").strip() or "поставка"
    safe = _INVALID_FILENAME.sub("_", f"Поставка {number} листы.pdf").strip(" .")
    return safe or "pallet_sheets.pdf"


def generate_wb_fbw_pallet_sheets_pdf(data: WbFbwPalletSheetData) -> bytes:
    supply_id = str(data.supply_id or "").strip()
    if not supply_id:
        raise ValueError("Нет номера поставки для листов")
    city = str(data.city or "").strip()
    if not city:
        raise ValueError("Нет города для листов паллет")
    pallet_count = parse_pallet_count(data.pallet_count)

    _regular, bold_font = get_pdf_label_fonts()
    buf = io.BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    for index in range(1, pallet_count + 1):
        lines = [
            "WILDBERRIES",
            f"Поставка № {supply_id}",
            city,
            f"Палет {index} из {pallet_count}",
        ]
        sizes = [_fit_font_size(c, bold_font, line, page_w - 40) for line in lines]
        block_h = sum(size * (_LEADING / _FONT_SIZE) for size in sizes)
        y = (page_h + block_h) / 2 - sizes[0] * 0.78
        for line, size in zip(lines, sizes, strict=True):
            c.setFont(bold_font, size)
            c.drawCentredString(page_w / 2, y, line)
            y -= size * (_LEADING / _FONT_SIZE)
        c.showPage()
    c.save()
    return buf.getvalue()


def _fit_font_size(c: canvas.Canvas, font_name: str, text: str, max_width: float) -> float:
    size = float(_FONT_SIZE)
    width = c.stringWidth(text, font_name, size)
    if width <= max_width or not text:
        return size
    return max(28.0, size * max_width / width)
