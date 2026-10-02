"""Упаковочные листы паллет FBO WB new по образцу из кабинета."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts
from app.wb_fbw_pallet_sheets import parse_pallet_count


@dataclass(frozen=True)
class WbFboPackingSheetData:
    supply_id: str
    warehouse_name: str
    seller_name: str
    plan_date: str
    supply_type: str
    print_count: int
    pallet_total: int


def _required(raw: object, message: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(message)
    return value


def _display_date(raw: object) -> str:
    value = str(raw or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return value


def _display_supply_type(raw: object) -> str:
    value = str(raw or "").strip()
    if value.casefold().startswith("монопал"):
        return "Монопаллет"
    return value


def generate_wb_fbo_packing_sheets_pdf(data: WbFboPackingSheetData) -> bytes:
    supply_id = _required(data.supply_id, "В QR поставки нет номера поставки")
    warehouse = _required(data.warehouse_name, "В QR поставки нет склада назначения")
    seller = _required(data.seller_name, "В QR поставки нет наименования юридического лица")
    plan_date = _required(_display_date(data.plan_date), "В QR поставки нет даты поставки")
    supply_type = _required(
        _display_supply_type(data.supply_type),
        "В QR поставки нет типа упаковки",
    )
    print_count = parse_pallet_count(data.print_count)
    pallet_total = parse_pallet_count(data.pallet_total)
    if print_count > pallet_total:
        raise ValueError("Количество листов для печати не может быть больше общего количества паллет")

    regular_font, bold_font = get_pdf_label_fonts()
    page_w, page_h = A4
    left = 85
    right = 43
    max_width = page_w - left - right
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)

    for pallet_number in range(1, print_count + 1):
        pdf.setFont(bold_font, 70)
        pdf.drawCentredString(page_w / 2, page_h - 105, "WILDBERRIES")
        pdf.setFont(regular_font, 40)
        pdf.drawCentredString(page_w / 2, page_h - 180, "Упаковочный лист")

        details = [
            ("Номер паллеты", str(pallet_number)),
            ("Количество паллет в поставке", str(pallet_total)),
            ("Номер поставки", supply_id),
            ("Склад назначения", warehouse),
            ("Тип упаковки", supply_type),
            ("Наименование Юридического лица", seller),
            ("Дата поставки", f"{plan_date} г."),
        ]
        y = page_h - 285
        for label, value in details:
            _draw_detail(
                pdf,
                regular_font,
                bold_font,
                label,
                value,
                left,
                y,
                max_width,
            )
            y -= 62
        pdf.showPage()

    pdf.save()
    return buf.getvalue()


def _draw_detail(
    pdf: canvas.Canvas,
    regular_font: str,
    bold_font: str,
    label: str,
    value: str,
    x: float,
    y: float,
    max_width: float,
) -> None:
    size = 25.0
    separator = " – "
    while size > 14:
        width = (
            pdf.stringWidth(label + separator, regular_font, size)
            + pdf.stringWidth(value, bold_font, size)
        )
        if width <= max_width:
            break
        size -= 1
    pdf.setFont(regular_font, size)
    prefix = label + separator
    pdf.drawString(x, y, prefix)
    pdf.setFont(bold_font, size)
    pdf.drawString(x + pdf.stringWidth(prefix, regular_font, size), y, value)
