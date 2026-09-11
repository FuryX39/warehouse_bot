"""Этикетки коробов FBW 58×40 мм — макет как в кабинете WB (QR, не Code 128)."""

from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any

from reportlab.graphics.barcode.qr import QrCode
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.pdf_fonts import get_pdf_label_fonts

LABEL_WIDTH_MM = 58.0
LABEL_HEIGHT_MM = 40.0

_BOX_TYPE_BY_ID = {
    0: "Виртуальная",
    1: "Короб",
    2: "Короб",
    5: "Монопаллета",
    6: "Суперсейф",
}

# Координаты с официальной этикетки 58×40 мм (pt, origin сверху слева у WB).
_LEFT = 5.67
_COL_X = 59.53
_RIGHT_INSET = 7.7
_QR_BIG_SIZE = 48.19
_QR_BIG_TOP = 19.0
_QR_SMALL_SIZE = 22.68
_QR_SMALL_LEFT = 136.06
_QR_SMALL_TOP = 83.62
_LABEL_PT = 5.75
_VALUE_PT = 6.5
_BOX_PREFIX_PT = 8.0
_BOX_REST_PT = 9.5
_LINE_STEP = 7.63
_FIELD_GAP = 4.27
# DejaVu: верх глифа ≈ baseline - 0.78*size; у ALS Hauss bbox выше.
_ASCENT = 0.78


def build_fbw_box_label_rows(
    *,
    supply_id: int,
    goods: list[dict[str, Any]],
    boxes: list[dict[str, Any]],
    supply: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sku_by_barcode = {
        str(row.get("barcode") or "").strip(): str(row.get("vendorCode") or "").strip()
        for row in goods
        if str(row.get("barcode") or "").strip()
    }
    meta = supply or {}
    total = len(boxes)
    plan_date = format_fbw_plan_date(meta.get("supplyDate"))
    warehouse = str(meta.get("warehouseName") or "").strip()
    seller = str(
        meta.get("sellerName") or meta.get("supplierAssignName") or ""
    ).strip()
    box_type = fbw_box_type_label(meta)
    rows: list[dict[str, Any]] = []
    for index, box in enumerate(boxes, start=1):
        items = [item for item in (box.get("barcodes") or []) if isinstance(item, dict)]
        first = items[0] if items else {}
        product_barcode = str(first.get("barcode") or "").strip()
        item_qty = first.get("quantity")
        package_code = str(box.get("packageCode") or "").strip()
        rows.append(
            {
                "supply_id": int(supply_id),
                "index": index,
                "total": total,
                "package_code": package_code,
                "quantity": int(box.get("quantity") or item_qty or 0),
                "product_barcode": product_barcode,
                "sku": sku_by_barcode.get(product_barcode, ""),
                "lines": len(items),
                "box_id": parse_fbw_box_id_from_package_code(package_code),
                "plan_date": plan_date,
                "warehouse": warehouse,
                "seller": seller,
                "box_type": box_type,
            }
        )
    return rows


def fbw_box_type_label(supply: dict[str, Any] | None) -> str:
    meta = supply or {}
    if meta.get("isBoxOnPallet") is True:
        return "Палета"
    try:
        box_type_id = int(meta.get("boxTypeID"))
    except (TypeError, ValueError):
        box_type_id = 1
    return _BOX_TYPE_BY_ID.get(box_type_id, "Короб")


def parse_fbw_box_id_from_package_code(package_code: object) -> str:
    """Номер короба с этикетки кабинета WB, из ``packageCode`` supplies-api.

    Кабинет печатает ``413 9894``, API отдаёт только
    ``$Ts;<a>;<b>;<type>;<payload>;TAS``. ``payload`` — ordinary base64;
    первые 3 байта big-endian и есть этот номер. Это не отдельное поле API.

    Проверено по официальным PDF: поставка 41357389 (15 этикеток) и
    41322982 (240 этикеток).
    """
    text = str(package_code or "").strip()
    if not text.startswith("$Ts;") or not text.endswith(";TAS"):
        return ""
    parts = text.split(";")
    if len(parts) < 6:
        return ""
    payload = parts[4].strip()
    if not payload:
        return ""
    padded = payload + "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.b64decode(padded, validate=False)
    except Exception:
        return ""
    if len(raw) < 3:
        return ""
    return str(int.from_bytes(raw[:3], "big"))


def format_fbw_box_human_id(box_id: object) -> str:
    digits = "".join(ch for ch in str(box_id or "") if ch.isdigit())
    if len(digits) <= 3:
        return digits
    return f"{digits[:3]} {digits[3:]}"


def format_fbw_plan_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    head = raw.replace("Z", "")
    if "T" in head:
        head = head.split("T", 1)[0]
    try:
        return datetime.strptime(head[:10], "%Y-%m-%d").strftime("%d.%m.%y")
    except ValueError:
        return raw


def generate_wb_fbw_box_labels_pdf(rows: list[dict[str, Any]]) -> bytes:
    """Один PDF: страница на короб, 58×40 мм, макет кабинета WB."""
    if not rows:
        raise ValueError("Нет коробов для этикеток")
    page_w = LABEL_WIDTH_MM * mm
    page_h = LABEL_HEIGHT_MM * mm
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))
    font, font_bold = get_pdf_label_fonts()
    for row in rows:
        _draw_box_label(c, row, page_w=page_w, page_h=page_h, font=font, font_bold=font_bold)
        c.showPage()
    c.save()
    return pdf_buf.getvalue()


def _draw_box_label(
    c: canvas.Canvas,
    row: dict[str, Any],
    *,
    page_w: float,
    page_h: float,
    font: str,
    font_bold: str,
) -> None:
    code = str(row.get("package_code") or "").strip()
    if not code:
        raise ValueError("У короба нет packageCode")

    box_id = format_fbw_box_human_id(
        row.get("box_id") or parse_fbw_box_id_from_package_code(code)
    )
    if box_id:
        prefix, _sep, rest = box_id.partition(" ")
        y_id = _text_y(page_h, top=4.57, size=_BOX_REST_PT)
        c.setFont(font, _BOX_PREFIX_PT)
        c.drawString(_LEFT, y_id, prefix)
        if rest:
            prefix_w = pdfmetrics.stringWidth(prefix, font, _BOX_PREFIX_PT)
            c.setFont(font, _BOX_REST_PT)
            c.drawString(_LEFT + prefix_w + 1.4, y_id, rest)

    qr_big_y = page_h - _QR_BIG_TOP - _QR_BIG_SIZE
    _draw_qr(c, code, _LEFT, qr_big_y, _QR_BIG_SIZE)
    qr_small_y = page_h - _QR_SMALL_TOP - _QR_SMALL_SIZE
    _draw_qr(c, code, _QR_SMALL_LEFT, qr_small_y, _QR_SMALL_SIZE)

    qr_cx = _LEFT + _QR_BIG_SIZE / 2
    qty = int(row.get("quantity") or 0)
    box_type = str(row.get("box_type") or "Короб").strip() or "Короб"
    c.setFont(font, _LABEL_PT)
    c.drawCentredString(qr_cx, _text_y(page_h, 70.64, _LABEL_PT), "Тип поставки:")
    c.setFont(font_bold, _VALUE_PT)
    c.drawCentredString(qr_cx, _text_y(page_h, 78.27, _VALUE_PT), box_type)
    c.setFont(font, _LABEL_PT)
    c.drawCentredString(qr_cx, _text_y(page_h, 91.05, _LABEL_PT), "Кол-во товаров:")
    c.setFont(font_bold, _VALUE_PT)
    c.drawCentredString(qr_cx, _text_y(page_h, 98.68, _VALUE_PT), f"{qty} шт" if qty else "")

    max_x = page_w - _RIGHT_INSET
    y = _text_y(page_h, 18.85, _LABEL_PT)
    y = _draw_labeled_field(
        c,
        x=_COL_X,
        y=y,
        label="№ поставки:",
        value=str(row.get("supply_id") or "").strip(),
        max_x=max_x,
        font=font,
        font_bold=font_bold,
    )
    y = _draw_labeled_field(
        c,
        x=_COL_X,
        y=y,
        label="Плановая дата:",
        value=str(row.get("plan_date") or "").strip(),
        max_x=max_x,
        font=font,
        font_bold=font_bold,
    )
    y = _draw_labeled_field(
        c,
        x=_COL_X,
        y=y,
        label="Пункт отгрузки:",
        value=str(row.get("warehouse") or "").strip(),
        max_x=max_x,
        font=font,
        font_bold=font_bold,
    )
    _draw_labeled_field(
        c,
        x=_COL_X,
        y=y,
        label="Продавец:",
        value=str(row.get("seller") or "").strip(),
        max_x=max_x,
        font=font,
        font_bold=font_bold,
    )


def _draw_labeled_field(
    c: canvas.Canvas,
    *,
    x: float,
    y: float,
    label: str,
    value: str,
    max_x: float,
    font: str,
    font_bold: str,
) -> float:
    lines = _wrap_labeled_value(
        label,
        value,
        label_font=font,
        value_font=font_bold,
        col_x=x,
        max_x=max_x,
    )
    cursor = y
    for index, (line_label, line_value) in enumerate(lines):
        if line_label:
            c.setFont(font, _LABEL_PT)
            c.drawString(x, cursor, line_label)
            label_w = pdfmetrics.stringWidth(line_label, font, _LABEL_PT)
            if line_value:
                c.setFont(font_bold, _VALUE_PT)
                c.drawString(x + label_w, cursor, " " + line_value)
        elif line_value:
            c.setFont(font_bold, _VALUE_PT)
            c.drawString(x, cursor, line_value)
        if index + 1 < len(lines):
            cursor -= _LINE_STEP
    return cursor - _LINE_STEP - _FIELD_GAP


def _wrap_labeled_value(
    label: str,
    value: str,
    *,
    label_font: str,
    value_font: str,
    col_x: float,
    max_x: float,
) -> list[tuple[str, str]]:
    value = " ".join(str(value or "").split())
    if not value:
        return [(label, "")]

    def value_width(text: str) -> float:
        return pdfmetrics.stringWidth(text, value_font, _VALUE_PT)

    label_w = pdfmetrics.stringWidth(label, label_font, _LABEL_PT)
    space_w = pdfmetrics.stringWidth(" ", value_font, _VALUE_PT)
    first_avail = max(8.0, max_x - col_x - label_w - space_w)
    next_avail = max(8.0, max_x - col_x)
    words = value.split(" ")
    chunks: list[str] = []
    current = ""
    avail = first_avail
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if value_width(candidate) <= avail:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
            avail = next_avail
        if value_width(word) <= avail:
            current = word
            continue
        piece = word
        while piece:
            take = piece
            while len(take) > 1 and value_width(take) > avail:
                take = take[:-1]
            chunks.append(take)
            piece = piece[len(take) :]
            avail = next_avail
            current = ""
    if current:
        chunks.append(current)
    if not chunks:
        return [(label, value)]
    lines = [(label, chunks[0])]
    lines.extend(("", chunk) for chunk in chunks[1:])
    return lines


def _text_y(page_h: float, top: float, size: float) -> float:
    return page_h - top - size * _ASCENT


def _draw_qr(c: canvas.Canvas, value: str, x: float, y: float, size: float) -> None:
    # Тихий зона — поля этикетки, как в кабинете: QR вплотную к квадрату.
    # Кабинет WB: коррекция M. Маску библиотека выбирает сама — картинка может отличаться.
    widget = QrCode(value=value, width=size, height=size, qrBorder=0, qrLevel="M")
    widget.drawOn(c, x, y)
