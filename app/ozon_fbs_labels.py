"""Заказы Ozon «ожидают отгрузки» и PDF-этикетки FBS."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.ozon import OZON_AWAITING_SHIPMENT_STATUSES, OzonAdapter, OzonFbsPosting
from app.google_sheet_write import fbs_list_sheet_title, write_fbs_list_sheet
from app.services import StockCoordinator

_FBS_SHEET_HEADER = ["№", "Отправление", "Артикул", "Кол-во", "Статус"]


@dataclass(frozen=True)
class OzonFbsListRow:
    seq: int
    posting_number: str
    sku: str
    quantity: int
    status: str


@dataclass
class OzonAwaitingShipmentBundle:
    postings: list[OzonFbsPosting] = field(default_factory=list)
    list_rows: list[OzonFbsListRow] = field(default_factory=list)
    label_files: list[tuple[str, bytes]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheet_title: str = ""
    sheet_url: str | None = None


def get_configured_ozon_adapter(coordinator: StockCoordinator) -> OzonAdapter | None:
    for adapter in coordinator.adapters:
        if isinstance(adapter, OzonAdapter) and adapter.is_configured():
            return adapter
    return None


def build_sorted_list_rows(postings: list[OzonFbsPosting]) -> list[OzonFbsListRow]:
    """Строки списка: одна на позицию в отправлении, сортировка по артикулу."""
    flat: list[tuple[str, str, int, str]] = []
    for posting in postings:
        for sku, qty in posting.lines:
            flat.append((sku, posting.posting_number, qty, posting.status))
    flat.sort(key=lambda x: (x[0].lower(), x[1]))
    return [
        OzonFbsListRow(i + 1, posting_number, sku, qty, status)
        for i, (sku, posting_number, qty, status) in enumerate(flat)
    ]


def posting_numbers_in_list_order(list_rows: list[OzonFbsListRow]) -> list[str]:
    """Уникальные отправления в порядке первого появления в отсортированном списке."""
    seen: set[str] = set()
    out: list[str] = []
    for row in list_rows:
        if row.posting_number in seen:
            continue
        seen.add(row.posting_number)
        out.append(row.posting_number)
    return out


def _merge_label_pdfs(pdf_parts: list[bytes]) -> bytes | None:
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


def _fetch_labels_in_order(
    adapter: OzonAdapter,
    posting_numbers: list[str],
) -> tuple[list[tuple[str, bytes]], list[str]]:
    label_files, warnings = adapter.fetch_package_label_pdf_parts(posting_numbers)
    pdfs = [data for _, data in label_files]
    merged = _merge_label_pdfs(pdfs)
    if merged is not None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return [(f"ozon_labels_sorted_{ts}.pdf", merged)], warnings
    if not label_files:
        return [], warnings
    if len(pdfs) > 1:
        warnings.append(
            "Не удалось объединить PDF в один файл (установите pypdf). "
            "Файлы в ZIP идут чанками API, порядок чанков сохранён."
        )
    return label_files, warnings


def _export_list_to_google_sheet(
    *,
    spreadsheet_url: str,
    credentials_path: str,
    sheet_title: str,
    list_rows: list[OzonFbsListRow],
) -> str:
    rows = [
        [str(r.seq), r.posting_number, r.sku, str(r.quantity), r.status]
        for r in list_rows
    ]
    return write_fbs_list_sheet(
        spreadsheet_url,
        credentials_path,
        sheet_title,
        _FBS_SHEET_HEADER,
        rows,
    )


def fetch_awaiting_shipment_labels(
    adapter: OzonAdapter,
    *,
    statuses: tuple[str, ...] = OZON_AWAITING_SHIPMENT_STATUSES,
    fbs_list_sheet_url: str = "",
    google_service_account_file: str = "",
) -> OzonAwaitingShipmentBundle:
    """Список по артикулу, этикетки в том же порядке, лист в Google Таблице."""
    postings = adapter.list_awaiting_shipment_postings(statuses=statuses)
    if not postings:
        return OzonAwaitingShipmentBundle()

    list_rows = build_sorted_list_rows(postings)
    posting_numbers = posting_numbers_in_list_order(list_rows)
    sheet_title = fbs_list_sheet_title()

    label_files, warnings = _fetch_labels_in_order(adapter, posting_numbers)

    sheet_url: str | None = None
    sheet_url_cfg = (fbs_list_sheet_url or "").strip()
    creds_path = (google_service_account_file or "").strip()
    if sheet_url_cfg and creds_path and list_rows:
        try:
            sheet_url = _export_list_to_google_sheet(
                spreadsheet_url=sheet_url_cfg,
                credentials_path=creds_path,
                sheet_title=sheet_title,
                list_rows=list_rows,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Google Таблица: {exc}")
    elif sheet_url_cfg and list_rows and not creds_path:
        warnings.append(
            "Google Таблица: задайте GOOGLE_SERVICE_ACCOUNT_FILE "
            "(JSON service account с доступом к FBS_LIST_SHEET_URL)."
        )
    elif not sheet_url_cfg and list_rows:
        warnings.append(
            "Google Таблица: задайте FBS_LIST_SHEET_URL (ссылка на таблицу для FBS-списков)."
        )

    postings_by_number = {p.posting_number: p for p in postings}
    ordered_postings = [
        postings_by_number[pn] for pn in posting_numbers if pn in postings_by_number
    ]

    return OzonAwaitingShipmentBundle(
        postings=ordered_postings,
        list_rows=list_rows,
        label_files=label_files,
        warnings=warnings,
        sheet_title=sheet_title,
        sheet_url=sheet_url,
    )


def format_postings_summary(
    postings: list[OzonFbsPosting] | None = None,
    *,
    list_rows: list[OzonFbsListRow] | None = None,
    max_lines: int = 40,
) -> str:
    if list_rows is not None:
        lines = [
            f"{r.seq}. {r.posting_number} — {r.sku}×{r.quantity} ({r.status})"
            for r in list_rows[:max_lines]
        ]
        total = len(list_rows)
    else:
        postings = postings or []
        lines = []
        for posting in postings[:max_lines]:
            items = ", ".join(f"{sku}×{qty}" for sku, qty in posting.lines)
            lines.append(f"{posting.posting_number} ({posting.status}): {items}")
        total = len(postings)
    if total > max_lines:
        lines.append(f"… ещё строк: {total - max_lines}")
    return "\n".join(lines)


def build_labels_zip(label_files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in label_files:
            zf.writestr(name, data)
    return buf.getvalue()
