"""PDF маршрутных листов для маркетплейсов."""

from __future__ import annotations

import io
import json
import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.pdf_fonts import get_pdf_label_fonts


DEFAULT_ROUTE_SUPPLIER = 'ООО "ШАЙН СИСТЕМС"'
DEFAULT_ROUTE_STATUSES = ("КЗ", "ПСО")
ROUTE_SHEET_MARKETPLACE_TITLES = {
    "vseinstrumenti": "ВсеИнструменты",
}
ROUTE_SHEET_CARGO_TYPES = {
    "pallets": {
        "title": "Паллеты",
        "count_label": "Кол-во паллет",
        "sequence_label": "Паллет из",
    },
    "boxes": {
        "title": "Короба",
        "count_label": "Кол-во коробов",
        "sequence_label": "Короб из",
    },
}
_STATUSES_LOCK = threading.Lock()
_STATUSES_FILE = Path(__file__).resolve().parent.parent / "data" / "route_sheet_purchase_statuses.json"
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def route_sheet_download_filename(marketplace_id: str, purchase_number: str) -> str:
    title = ROUTE_SHEET_MARKETPLACE_TITLES.get(marketplace_id, marketplace_id).strip()
    number = str(purchase_number or "").strip()
    parts = [part for part in (title, number) if part]
    raw = " ".join(parts) if parts else "маршрутный_лист"
    safe = _INVALID_FILENAME_CHARS.sub("_", raw).strip(" .")
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe}.pdf"
    return safe


def route_sheet_content_disposition(filename: str) -> str:
    raw = (filename or "route_sheet.pdf").strip() or "route_sheet.pdf"
    ascii_name = "".join(
        c if ord(c) < 128 and (c.isalnum() or c in "._- ") else "_" for c in raw
    ).strip() or "route_sheet.pdf"
    if not ascii_name.lower().endswith(".pdf"):
        ascii_name = f"{ascii_name}.pdf"
    utf8_name = quote(raw)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


@dataclass(frozen=True)
class VseinstrumentiRouteSheetData:
    supplier: str = DEFAULT_ROUTE_SUPPLIER
    purchase_number: str = ""
    purchase_status: str = ""
    delivery_date: str = ""
    cargo_type: str = "pallets"
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


def _statuses_storage_path() -> Path:
    path = _STATUSES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _default_status_rows() -> list[dict[str, Any]]:
    return [
        {"id": idx + 1, "name": name, "is_default": True}
        for idx, name in enumerate(DEFAULT_ROUTE_STATUSES)
    ]


def list_route_purchase_statuses() -> list[dict[str, Any]]:
    with _STATUSES_LOCK:
        path = _statuses_storage_path()
        if not path.is_file():
            return _default_status_rows()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_status_rows()
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return _default_status_rows()
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            row_id = item.get("id")
            try:
                row_id = int(row_id)
            except (TypeError, ValueError):
                row_id = idx + 1
            rows.append(
                {
                    "id": row_id,
                    "name": name,
                    "is_default": bool(item.get("is_default")),
                }
            )
        return rows or _default_status_rows()


def save_route_purchase_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    next_id = 1
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        raw_id = raw.get("id")
        try:
            row_id = int(raw_id)
        except (TypeError, ValueError):
            row_id = next_id
        next_id = max(next_id, row_id + 1)
        cleaned.append(
            {
                "id": row_id,
                "name": name,
                "is_default": bool(raw.get("is_default")),
            }
        )
    defaults = {name.casefold(): name for name in DEFAULT_ROUTE_STATUSES}
    existing = {row["name"].casefold(): row for row in cleaned}
    for name in DEFAULT_ROUTE_STATUSES:
        key = name.casefold()
        if key not in existing:
            cleaned.append({"id": next_id, "name": name, "is_default": True})
            next_id += 1
        else:
            existing[key]["is_default"] = True
            existing[key]["name"] = defaults.get(key, existing[key]["name"])
    cleaned.sort(
        key=lambda row: (
            0 if row["is_default"] else 1,
            DEFAULT_ROUTE_STATUSES.index(row["name"])
            if row["name"] in DEFAULT_ROUTE_STATUSES
            else 999,
            row["name"].casefold(),
        )
    )
    with _STATUSES_LOCK:
        _statuses_storage_path().write_text(
            json.dumps({"items": cleaned}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return list_route_purchase_statuses()


def normalize_vseinstrumenti_route_sheet_payload(payload: dict) -> VseinstrumentiRouteSheetData:
    supplier = _clean(payload.get("supplier")) or DEFAULT_ROUTE_SUPPLIER
    purchase_number = _clean(payload.get("purchase_number"))
    purchase_status = _clean(payload.get("purchase_status"))
    delivery_date = _display_date(_clean(payload.get("delivery_date")))
    cargo_type = _clean(payload.get("cargo_type")).casefold() or "pallets"
    cargo_type = {
        "pallet": "pallets",
        "pallets": "pallets",
        "паллеты": "pallets",
        "паллет": "pallets",
        "box": "boxes",
        "boxes": "boxes",
        "короба": "boxes",
        "короб": "boxes",
    }.get(cargo_type, cargo_type)
    if cargo_type not in ROUTE_SHEET_CARGO_TYPES:
        raise ValueError("Выберите тип грузомест: паллеты или короба")
    raw_count = payload.get("cargo_count")
    if raw_count is None:
        raw_count = payload.get("pallet_count")
    cargo_title = ROUTE_SHEET_CARGO_TYPES[cargo_type]["title"].casefold()
    try:
        pallet_count = int(raw_count or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Количество ({cargo_title}) должно быть числом") from exc
    if pallet_count < 1:
        raise ValueError(f"Количество ({cargo_title}) должно быть не меньше 1")
    if pallet_count > 200:
        raise ValueError(f"Количество ({cargo_title}) слишком большое (макс. 200)")
    return VseinstrumentiRouteSheetData(
        supplier=supplier,
        purchase_number=purchase_number,
        purchase_status=purchase_status,
        delivery_date=delivery_date,
        cargo_type=cargo_type,
        pallet_count=pallet_count,
    )


def generate_vseinstrumenti_route_sheets_pdf(data: VseinstrumentiRouteSheetData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    regular_font, bold_font = get_pdf_label_fonts()
    buf = io.BytesIO()
    page_width, page_height = A4
    left_margin = 14 * mm
    right_margin = 14 * mm
    top_margin = 14 * mm
    bottom_margin = 14 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )
    story = []
    cargo_meta = ROUTE_SHEET_CARGO_TYPES[data.cargo_type]
    labels = [
        ("Поставщик", data.supplier),
        ("№ закупки", data.purchase_number),
        ("Статус закупки", data.purchase_status),
        ("Дата доставки", data.delivery_date),
        (cargo_meta["count_label"], str(data.pallet_count)),
    ]
    usable_width = page_width - left_margin - right_margin
    col_width = usable_width / 2
    font_size = 20
    row_height = 36 * mm
    usable_height = page_height - top_margin - bottom_margin
    for pallet_num in range(1, data.pallet_count + 1):
        rows = labels + [
            (cargo_meta["sequence_label"], f"{pallet_num}/{data.pallet_count}")
        ]
        table_height = row_height * len(rows)
        top_spacer = max(0, (usable_height - table_height) / 2)
        if top_spacer:
            story.append(Spacer(1, top_spacer))
        table = Table(
            rows,
            colWidths=[col_width, col_width],
            rowHeights=[row_height] * len(rows),
            hAlign="CENTER",
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), regular_font),
                    ("FONTNAME", (0, 0), (0, -1), bold_font),
                    ("FONTSIZE", (0, 0), (-1, -1), font_size),
                    ("GRID", (0, 0), (-1, -1), 1.4, colors.black),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("WORDWRAP", (0, 0), (-1, -1), True),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(table)
        if pallet_num < data.pallet_count:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()
