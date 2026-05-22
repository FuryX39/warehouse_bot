"""Общие утилиты FBS-этикеток (PDF merge, ZIP)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

_TPosting = TypeVar("_TPosting")


def build_fbs_sorted_flat_rows(
    postings: Sequence[_TPosting],
    *,
    iter_lines: Callable[[_TPosting], Iterable[tuple[str, int]]],
    get_posting_key: Callable[[_TPosting], str],
    get_status: Callable[[_TPosting], str],
) -> list[tuple[str, str, int, str]]:
    """Строки (sku, posting_key, qty, status): одиночные позиции по артикулу, мульти — в конце как в API."""
    single_rows: list[tuple[str, str, int, str]] = []
    multi_rows: list[tuple[str, str, int, str]] = []

    for posting in postings:
        lines = list(iter_lines(posting))
        posting_key = get_posting_key(posting)
        status = get_status(posting)
        bucket = multi_rows if len(lines) >= 2 else single_rows
        for sku, qty in lines:
            bucket.append((sku, posting_key, qty, status))

    single_rows.sort(key=lambda x: x[0].lower())
    return single_rows + multi_rows


def split_pdf_into_pages(pdf_bytes: bytes) -> list[bytes]:
    """Один PDF → список одностраничных PDF (порядок страниц сохраняется)."""
    if not pdf_bytes.startswith(b"%PDF"):
        return [pdf_bytes]
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return [pdf_bytes]
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[bytes] = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        pages.append(buf.getvalue())
    return pages


def merge_label_pdfs(pdf_parts: list[bytes]) -> bytes | None:
    if not pdf_parts:
        return None
    if len(pdf_parts) == 1:
        return pdf_parts[0]
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return None
    writer = PdfWriter()
    for pdf in pdf_parts:
        reader = PdfReader(io.BytesIO(pdf))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_labels_zip(label_files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in label_files:
            zf.writestr(name, data)
    return buf.getvalue()
