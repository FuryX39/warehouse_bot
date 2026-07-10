"""PDF маршрутных листов для маркетплейсов."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from app.pdf_fonts import get_pdf_label_fonts


DEFAULT_ROUTE_SUPPLIER = 'ООО "ШАЙН СИСТЕМС"'
DEFAULT_ROUTE_STATUSES = ("ПСО", "КЗ")


@dataclass(frozen=True)
class VseinstrumentiRouteSheetData:
    supplier: str = DEFAULT_ROUTE_SUPPLIER
    purchase_number: str = ""
    purchase_status: str = ""
    delivery_date: str = ""
    pallet_count: int = 1


def _clean(value: object) -> str:
    return str(value or "").strip()


def _display_date(value: str) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).strftime("%d.%m.%Y")
    except ValueError:
        return raw


def normalize_vseinstrumenti_route_sheet_payload(payload: dict) -> VseinstrumentiRouteSheetData:
    supplier = _clean(payload.get("supplier")) or DEFAULT_ROUTE_SUPPLIER
    purchase_number = _clean(payload.get("purchase_number"))
    purchase_status = _clean(payload.get("purchase_status"))
    delivery_date = _display_date(_clean(payload.get("delivery_date")))
    try:
        pallet_count = int(payload.get("pallet_count") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Кол-во паллет должно быть числом") from exc
    if pallet_count < 1:
        raise ValueError("Кол-во паллет должно быть не меньше 1")
    if pallet_count > 200:
        raise ValueError("Кол-во паллет слишком большое (макс. 200)")
    return VseinstrumentiRouteSheetData(
        supplier=supplier,
        purchase_number=purchase_number,
        purchase_status=purchase_status,
        delivery_date=delivery_date,
        pallet_count=pallet_count,
    )


def generate_vseinstrumenti_route_sheets_pdf(data: VseinstrumentiRouteSheetData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    regular_font, bold_font = get_pdf_label_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    story = []
    labels = [
        ("Поставщик", data.supplier),
        ("№ закупки", data.purchase_number),
        ("Статус закупки", data.purchase_status),
        ("Дата доставки", data.delivery_date),
        ("Кол-во паллет", str(data.pallet_count)),
    ]
    for pallet_num in range(1, data.pallet_count + 1):
        rows = labels + [("Паллет из", f"{pallet_num}/{data.pallet_count}")]
        table = Table(rows, colWidths=[62 * mm, 112 * mm], rowHeights=[19 * mm] * len(rows))
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), regular_font),
                    ("FONTNAME", (0, 0), (0, -1), bold_font),
                    ("FONTSIZE", (0, 0), (-1, -1), 15),
                    ("GRID", (0, 0), (-1, -1), 1.2, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        if pallet_num < data.pallet_count:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 1 * mm))
    doc.build(story)
    return buf.getvalue()
