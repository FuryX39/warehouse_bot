"""PDF маршрутных листов для маркетплейсов."""

from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.pdf_fonts import get_pdf_label_fonts


DEFAULT_ROUTE_SUPPLIER = 'ООО "ШАЙН СИСТЕМС"'
DEFAULT_ROUTE_STATUSES = ("КЗ", "ПСО")
_STATUSES_LOCK = threading.Lock()
_STATUSES_FILE = Path(__file__).resolve().parent.parent / "data" / "route_sheet_purchase_statuses.json"


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
    labels = [
        ("Поставщик", data.supplier),
        ("№ закупки", data.purchase_number),
        ("Статус закупки", data.purchase_status),
        ("Дата доставки", data.delivery_date),
        ("Кол-во паллет", str(data.pallet_count)),
    ]
    usable_width = page_width - left_margin - right_margin
    label_col_width = usable_width * 0.34
    value_col_width = usable_width - label_col_width
    font_size = 24
    row_height = 36 * mm
    usable_height = page_height - top_margin - bottom_margin
    for pallet_num in range(1, data.pallet_count + 1):
        rows = labels + [("Паллет из", f"{pallet_num}/{data.pallet_count}")]
        table_height = row_height * len(rows)
        top_spacer = max(0, (usable_height - table_height) / 2)
        if top_spacer:
            story.append(Spacer(1, top_spacer))
        table = Table(
            rows,
            colWidths=[label_col_width, value_col_width],
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
