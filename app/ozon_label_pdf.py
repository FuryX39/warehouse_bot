"""Постобработка PDF-этикеток Ozon FBS (ориентация штрихкодов для печати)."""

from __future__ import annotations

import io


def rotate_pdf_pages(pdf_bytes: bytes, degrees: int) -> bytes:
    """Поворачивает все страницы PDF (degrees: 90, -90, 180, …)."""
    if not degrees or degrees % 360 == 0:
        return pdf_bytes
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(degrees)
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def normalize_ozon_package_label_pdf(
    pdf_bytes: bytes,
    *,
    rotate_degrees: int = 90,
    strict: bool = False,
) -> bytes:
    """
    Ozon отдаёт этикетки с «вертикальными» штрихкодами на листе;
    поворот на 90° — для печати на термопринтере в альбомной ориентации.
    """
    if not pdf_bytes.startswith(b"%PDF"):
        return pdf_bytes
    try:
        return rotate_pdf_pages(pdf_bytes, rotate_degrees)
    except Exception:
        if strict:
            raise
        return pdf_bytes
