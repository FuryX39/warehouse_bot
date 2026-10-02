"""QR поставки FBW (WB-GI-…): разбор из текста и PDF кабинета."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime

_GI_RE = re.compile(r"WB-GI-?\s*([\d\s]+)", re.IGNORECASE)
_GI_COMPACT_RE = re.compile(r"^WB-GI-\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class WbSupplyQrData:
    qr_code: str = ""
    supply_id: str = ""
    warehouse_name: str = ""
    seller_name: str = ""
    plan_date: str = ""
    supply_type: str = ""
    pallet_count: int = 0


def normalize_wb_supply_qr_code(raw: object) -> str:
    """`WB-GI-27768 9956` и `WB-GI-277689956` → `WB-GI-277689956`."""
    text = str(raw or "").replace("\u00a0", " ").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text.upper())
    if _GI_COMPACT_RE.fullmatch(compact):
        return f"WB-GI-{compact.split('-', 2)[-1]}"
    match = _GI_RE.search(text)
    if not match:
        return ""
    digits = re.sub(r"\D", "", match.group(1))
    return f"WB-GI-{digits}" if digits else ""


def _pdf_text(content: bytes) -> str:
    if not content or not content.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        return ""
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks)


def _field(
    text: str,
    label: str,
    *,
    until: str | None = None,
    to_end: bool = False,
) -> str:
    if until:
        end = rf"(?=\n\s*{re.escape(until)}\s*:|\Z)"
    else:
        end = r"\Z" if to_end else r"(?=\n|\Z)"
    match = re.search(rf"{re.escape(label)}\s*:\s*(.+?){end}", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _full_date(raw: str) -> str:
    value = str(raw or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return value


def extract_wb_supply_data_from_pdf(content: bytes) -> WbSupplyQrData:
    text = _pdf_text(content)
    if not text:
        return WbSupplyQrData()
    count_match = re.search(r"(\d+)\s*шт\.?\s*паллет", text, re.IGNORECASE)
    supply_match = re.search(r"№\s*поставки\s*:\s*(\d+)", text, re.IGNORECASE)
    date_match = re.search(
        r"Плановая\s+дата\s*:\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
        text,
        re.IGNORECASE,
    )
    return WbSupplyQrData(
        qr_code=normalize_wb_supply_qr_code(text),
        supply_id=supply_match.group(1) if supply_match else "",
        warehouse_name=_field(text, "Пункт отгрузки", until="Продавец"),
        seller_name=_field(text, "Продавец", to_end=True),
        plan_date=_full_date(date_match.group(1) if date_match else ""),
        supply_type=_field(text, "Тип поставки"),
        pallet_count=int(count_match.group(1)) if count_match else 0,
    )


def extract_wb_supply_qr_code_from_pdf(content: bytes) -> str:
    return extract_wb_supply_data_from_pdf(content).qr_code
