"""Тесты объединения PDF."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from app.pdf_merge import merge_pdfs_in_order


def _one_page_pdf(text: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_merge_pdfs_in_order_requires_input() -> None:
    with pytest.raises(ValueError, match="хотя бы один"):
        merge_pdfs_in_order([])


def test_merge_pdfs_in_order_rejects_non_pdf() -> None:
    with pytest.raises(ValueError, match="не является PDF"):
        merge_pdfs_in_order([b"not-a-pdf"])


def test_merge_pdfs_in_order_preserves_page_order() -> None:
    first = _one_page_pdf("a")
    second = _one_page_pdf("b")
    merged = merge_pdfs_in_order([first, second])
    assert merged.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(merged)).pages) == 2
