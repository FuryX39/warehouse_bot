"""Упаковочные листы паллет FBO WB new по образцу Word A4."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts
from app.wb_fbw_pallet_sheets import parse_pallet_count

# Word: A4 portrait, WILDBERRIES 70, подзаголовок 40, поля 25, слева 42.5 pt.
_LEFT = 42.5
_RIGHT = 50.0
_BODY_SIZE = 25.0
_FIELD_LEADING = 40.9
_WRAP_LEADING = 32.9
_WB_FROM_TOP = 109.2
_SUBTITLE_FROM_TOP = 241.4
_FIRST_FIELD_FROM_TOP = 348.6
_LEGAL_FORMS = frozenset({"ООО", "ОАО", "ЗАО", "ПАО", "ИП", "АО", "НАО", "НКО"})
_QUOTE_CHARS = str.maketrans({"«": '"', "»": '"', "„": '"', "“": '"', "”": '"'})


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


def _pretty_company_words(text: str) -> str:
    parts: list[str] = []
    for word in text.split():
        prefix = "«" if word.startswith("«") else ""
        suffix = "»" if word.endswith("»") else ""
        core = word[len(prefix) : len(word) - len(suffix) if suffix else len(word)]
        if core.upper() in _LEGAL_FORMS:
            core = core.upper()
        elif core.isupper() and any(ch.isalpha() for ch in core):
            core = core[:1] + core[1:].lower()
        parts.append(f"{prefix}{core}{suffix}")
    return " ".join(parts)


def display_legal_name(raw: object) -> str:
    """`ООО \"ШАЙН СИСТЕМС\"` → `ООО «Шайн Системс»`, как в образце Word."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return ""
    text = text.translate(_QUOTE_CHARS)

    def _quoted(match: re.Match[str]) -> str:
        inner = _pretty_company_words(match.group(1).strip())
        return f"«{inner}»" if inner else "«»"

    text = re.sub(r'"([^"]*)"', _quoted, text)
    return _pretty_company_words(text)


def generate_wb_fbo_packing_sheets_pdf(data: WbFboPackingSheetData) -> bytes:
    supply_id = _required(data.supply_id, "В QR поставки нет номера поставки")
    warehouse = _required(data.warehouse_name, "В QR поставки нет склада назначения")
    seller = _required(display_legal_name(data.seller_name), "В QR поставки нет наименования юридического лица")
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
    max_width = page_w - _LEFT - _RIGHT
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)

    for pallet_number in range(1, print_count + 1):
        pdf.setFont(bold_font, 70)
        pdf.drawCentredString(page_w / 2, page_h - _WB_FROM_TOP, "WILDBERRIES")
        pdf.setFont(regular_font, 40)
        pdf.drawCentredString(page_w / 2, page_h - _SUBTITLE_FROM_TOP, "Упаковочный лист")

        details = [
            ("Номер паллеты", str(pallet_number)),
            ("Количество паллет в поставке", str(pallet_total)),
            ("Номер поставки", supply_id),
            ("Склад назначения", warehouse),
            ("Тип упаковки", supply_type),
            ("Наименование Юридического лица", seller),
            ("Дата поставки", f"{plan_date} г."),
        ]
        y = page_h - _FIRST_FIELD_FROM_TOP
        for label, value in details:
            y = _draw_detail(
                pdf,
                regular_font,
                bold_font,
                label,
                value,
                _LEFT,
                y,
                max_width,
            )
        pdf.showPage()

    pdf.save()
    return buf.getvalue()


def _take_words(
    pdf: canvas.Canvas,
    words: list[str],
    font: str,
    size: float,
    max_width: float,
    prefix_width: float,
) -> tuple[list[str], list[str]]:
    taken: list[str] = []
    remain = list(words)
    while remain:
        trial = " ".join(taken + [remain[0]])
        width = prefix_width + pdf.stringWidth(trial, font, size)
        if taken and width > max_width:
            break
        taken.append(remain.pop(0))
        if width > max_width:
            break
    return taken, remain


def _draw_detail(
    pdf: canvas.Canvas,
    regular_font: str,
    bold_font: str,
    label: str,
    value: str,
    x: float,
    y: float,
    max_width: float,
) -> float:
    size = _BODY_SIZE
    prefix = f"{label} – "
    prefix_width = pdf.stringWidth(prefix, regular_font, size)
    words = str(value or "").split() or [""]
    first, remain = _take_words(pdf, words, bold_font, size, max_width, prefix_width)
    pdf.setFont(regular_font, size)
    pdf.drawString(x, y, prefix)
    pdf.setFont(bold_font, size)
    pdf.drawString(x + prefix_width, y, " ".join(first))
    if not remain:
        return y - _FIELD_LEADING
    y -= _WRAP_LEADING
    while remain:
        line, remain = _take_words(pdf, remain, bold_font, size, max_width, 0)
        pdf.setFont(bold_font, size)
        pdf.drawString(x, y, " ".join(line))
        y -= _WRAP_LEADING if remain else _FIELD_LEADING
    return y
