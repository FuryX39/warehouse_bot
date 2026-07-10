"""Объединение PDF-файлов в один документ."""

from __future__ import annotations

from app.fbs_labels_common import merge_label_pdfs


def merge_pdfs_in_order(pdf_parts: list[bytes]) -> bytes:
    if not pdf_parts:
        raise ValueError("Загрузите хотя бы один PDF")
    for idx, pdf in enumerate(pdf_parts, start=1):
        if not pdf.startswith(b"%PDF"):
            raise ValueError(f"Файл №{idx} не является PDF")
    merged = merge_label_pdfs(pdf_parts)
    if merged is None:
        raise ValueError("Не удалось объединить PDF (pypdf)")
    return merged
