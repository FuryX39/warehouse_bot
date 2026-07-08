"""Заказы Ozon «ожидают отгрузки» и PDF-этикетки FBS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.ozon import OZON_AWAITING_SHIPMENT_STATUSES, OzonAdapter, OzonFbsPosting
from app.fbs_assembly_order import apply_assembly_order_to_ozon_rows
from app.fbs_labels_common import build_fbs_sorted_flat_rows, build_labels_zip, merge_label_pdfs
from app.google_sheet_write import fbs_list_sheet_title, write_fbs_list_from_template
from app.ozon_label_pdf import normalize_ozon_package_label_pdf
from app.services import StockCoordinator

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
    """Строки списка: одна на позицию; одиночные отправления — по артикулу, 2+ SKU — в конце без сортировки."""
    flat = build_fbs_sorted_flat_rows(
        postings,
        iter_lines=lambda p: p.lines,
        get_posting_key=lambda p: p.posting_number,
        get_status=lambda p: p.status,
    )
    return [
        OzonFbsListRow(i + 1, posting_number, sku, qty, status)
        for i, (sku, posting_number, qty, status) in enumerate(flat)
    ]


def apply_tsd_assembly_order(
    list_rows: list[OzonFbsListRow],
    *,
    default_stocks_sheet_url: str = "",
    google_service_account_file: str = "",
    assembly_sheet_name: str = "assembly",
    assembly_sheet_gid: int | None = None,
) -> tuple[list[OzonFbsListRow], list[str]]:
    """Переставить строки FBS в порядке листа assembly из bot_table (DEFAULT_STOCKS_SHEET_URL)."""
    return apply_assembly_order_to_ozon_rows(
        list_rows,
        default_stocks_sheet_url=default_stocks_sheet_url,
        google_service_account_file=google_service_account_file,
        assembly_sheet_name=assembly_sheet_name,
        assembly_sheet_gid=assembly_sheet_gid,
        row_factory=OzonFbsListRow,
    )


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


def posting_numbers_chronological(postings: list[OzonFbsPosting]) -> list[str]:
    """Номера отправлений по времени in_process_at: старые первые (как в ЛК Ozon сверху вниз)."""
    ordered = sorted(postings, key=lambda p: (p.in_process_at_ts, p.posting_number))
    return [p.posting_number for p in ordered]


def _normalize_posting_range_args(
    first_posting: str | None,
    last_posting: str | None,
) -> tuple[str | None, str | None]:
    first = (first_posting or "").strip() or None
    last = (last_posting or "").strip() or None
    if first and not last:
        last = first
    elif last and not first:
        first = last
    return first, last


def filter_by_posting_range(
    list_rows: list[OzonFbsListRow],
    posting_order: list[str],
    *,
    first_posting: str | None = None,
    last_posting: str | None = None,
) -> tuple[list[OzonFbsListRow], list[str]]:
    """
    Оставляет строки и отправления от first до last включительно
    в порядке posting_order (хронология Ozon: старый → новый).
    """
    first, last = _normalize_posting_range_args(first_posting, last_posting)
    if not first and not last:
        return list_rows, posting_order

    if first not in posting_order:
        raise ValueError(f"Первый номер отправления не найден в списке: {first}")
    if last not in posting_order:
        raise ValueError(f"Последний номер отправления не найден в списке: {last}")

    i0 = posting_order.index(first)
    i1 = posting_order.index(last)
    if i0 > i1:
        i0, i1 = i1, i0
        first, last = posting_order[i0], posting_order[i1]

    allowed = set(posting_order[i0 : i1 + 1])
    filtered_order = posting_order[i0 : i1 + 1]
    filtered_rows = [
        OzonFbsListRow(i + 1, r.posting_number, r.sku, r.quantity, r.status)
        for i, r in enumerate(row for row in list_rows if row.posting_number in allowed)
    ]
    return filtered_rows, filtered_order


def _fetch_labels_in_order(
    adapter: OzonAdapter,
    posting_numbers: list[str],
    *,
    label_rotate_degrees: int = 90,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Этикетки в порядке posting_numbers (как отправления на листе сборки)."""
    by_posting, warnings = adapter.fetch_package_label_by_posting(posting_numbers)
    ordered_pdfs: list[bytes] = []
    seen: set[str] = set()
    for pn in posting_numbers:
        pn = str(pn).strip()
        if not pn or pn in seen:
            continue
        seen.add(pn)
        raw = by_posting.get(pn)
        if raw is None:
            warnings.append(f"Нет этикетки для отправления {pn}")
            continue
        ordered_pdfs.append(
            normalize_ozon_package_label_pdf(raw, rotate_degrees=label_rotate_degrees)
        )
    if not ordered_pdfs:
        return [], warnings
    merged = merge_label_pdfs(ordered_pdfs)
    if merged is not None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return [(f"ozon_labels_sorted_{ts}.pdf", merged)], warnings
    if len(ordered_pdfs) > 1:
        warnings.append(
            "Не удалось объединить PDF в один файл (установите pypdf). "
            "Отправлены отдельные файлы по порядку листа."
        )
    return [(f"ozon_label_{i + 1}.pdf", pdf) for i, pdf in enumerate(ordered_pdfs)], warnings


def _export_list_to_google_sheet(
    *,
    spreadsheet_url: str,
    credentials_path: str,
    sheet_title: str,
    list_rows: list[OzonFbsListRow],
    template_sheet_name: str,
) -> str:
    data = [(r.sku, r.quantity, r.posting_number) for r in list_rows]
    return write_fbs_list_from_template(
        spreadsheet_url,
        credentials_path,
        sheet_title,
        data,
        template_sheet_name=template_sheet_name,
    )


def fetch_awaiting_shipment_labels(
    adapter: OzonAdapter,
    *,
    statuses: tuple[str, ...] = OZON_AWAITING_SHIPMENT_STATUSES,
    default_stocks_sheet_url: str = "",
    fbs_list_sheet_url: str = "",
    google_service_account_file: str = "",
    fbs_list_template_sheet: str = "FBSTemplate",
    fbs_assembly_sheet_name: str = "assembly",
    assembly_sheet_gid: int | None = None,
    ozon_label_rotate_degrees: int = 90,
    first_posting_number: str | None = None,
    last_posting_number: str | None = None,
) -> OzonAwaitingShipmentBundle:
    """Список FBS; порядок — assembly из bot_table; запись списка — в FBS_LIST_SHEET_URL."""
    postings = adapter.list_awaiting_shipment_postings(statuses=statuses)
    if not postings:
        return OzonAwaitingShipmentBundle()

    list_rows = build_sorted_list_rows(postings)
    posting_numbers = posting_numbers_chronological(postings)
    range_first, range_last = _normalize_posting_range_args(
        first_posting_number,
        last_posting_number,
    )
    if range_first or range_last:
        list_rows, posting_numbers = filter_by_posting_range(
            list_rows,
            posting_numbers,
            first_posting=range_first,
            last_posting=range_last,
        )
    if not list_rows:
        return OzonAwaitingShipmentBundle(
            warnings=[
                "После фильтра по номерам отправлений не осталось строк для списка."
            ],
        )

    list_rows, assembly_warnings = apply_tsd_assembly_order(
        list_rows,
        default_stocks_sheet_url=default_stocks_sheet_url,
        google_service_account_file=google_service_account_file,
        assembly_sheet_name=fbs_assembly_sheet_name,
        assembly_sheet_gid=assembly_sheet_gid,
    )

    sheet_title = fbs_list_sheet_title()
    label_posting_order = posting_numbers_in_list_order(list_rows)

    label_files, warnings = _fetch_labels_in_order(
        adapter,
        label_posting_order,
        label_rotate_degrees=ozon_label_rotate_degrees,
    )
    warnings = assembly_warnings + warnings

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
                template_sheet_name=(fbs_list_template_sheet or "FBSTemplate").strip(),
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
    if range_first or range_last:
        warnings.insert(
            0,
            f"Диапазон отправлений: {range_first} … {range_last} "
            f"({len(posting_numbers)} шт., по времени заказа).",
        )

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
            f"{r.seq}. {r.sku}×{r.quantity} — {r.posting_number}"
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


