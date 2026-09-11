"""QR поставки FBW (WB-GI-…): разбор из текста и PDF кабинета."""

from __future__ import annotations

import io
import re

_GI_RE = re.compile(r"WB-GI-?\s*([\d\s]+)", re.IGNORECASE)
_GI_COMPACT_RE = re.compile(r"^WB-GI-\d+$", re.IGNORECASE)


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


def extract_wb_supply_qr_code_from_pdf(content: bytes) -> str:
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
    return normalize_wb_supply_qr_code(" ".join(chunks))
