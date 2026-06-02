"""Заказы Yandex Market «собрано» и PDF-этикетки FBS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.yandex_market import (
    YANDEX_AWAITING_ASSEMBLY_SUBSTATUS,
    YandexFbsOrder,
    YandexMarketAdapter,
)
from app.fbs_labels_common import build_fbs_sorted_flat_rows, build_labels_zip, merge_label_pdfs
from app.google_sheet_write import fbs_list_sheet_title, write_fbs_list_from_template
from app.ozon_label_pdf import normalize_ozon_package_label_pdf
from app.services import StockCoordinator


@dataclass(frozen=True)
class YandexFbsListRow:
    seq: int
    order_id: str
    sku: str
    quantity: int
    status: str


@dataclass
class YandexAwaitingAssemblyBundle:
    orders: list[YandexFbsOrder] = field(default_factory=list)
    list_rows: list[YandexFbsListRow] = field(default_factory=list)
    label_files: list[tuple[str, bytes]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheet_title: str = ""
    sheet_url: str | None = None


def get_configured_yandex_adapter(coordinator: StockCoordinator) -> YandexMarketAdapter | None:
    for adapter in coordinator.adapters:
        if isinstance(adapter, YandexMarketAdapter) and adapter.is_configured():
            return adapter
    return None


def build_sorted_list_rows(orders: list[YandexFbsOrder]) -> list[YandexFbsListRow]:
    """Строки списка: одна на позицию; одиночные заказы — по артикулу, 2+ SKU — в конце без сортировки."""
    flat = build_fbs_sorted_flat_rows(
        orders,
        iter_lines=lambda o: o.lines,
        get_posting_key=lambda o: o.order_id,
        get_status=lambda o: o.substatus,
    )
    return [
        YandexFbsListRow(i + 1, order_id, sku, qty, status)
        for i, (sku, order_id, qty, status) in enumerate(flat)
    ]


def order_ids_in_list_order(list_rows: list[YandexFbsListRow]) -> list[str]:
    """Уникальные заказы в порядке первого появления в отсортированном списке."""
    seen: set[str] = set()
    out: list[str] = []
    for row in list_rows:
        if row.order_id in seen:
            continue
        seen.add(row.order_id)
        out.append(row.order_id)
    return out


def _fetch_labels_in_order(
    adapter: YandexMarketAdapter,
    order_ids: list[str],
    *,
    label_format: str = "A9_HORIZONTALLY",
    label_rotate_degrees: int = 0,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    label_files, warnings = adapter.fetch_order_label_pdf_parts(
        order_ids,
        label_format=label_format,
    )
    if label_rotate_degrees:
        label_files = [
            (name, normalize_ozon_package_label_pdf(data, rotate_degrees=label_rotate_degrees))
            for name, data in label_files
        ]
    pdfs = [data for _, data in label_files]
    merged = merge_label_pdfs(pdfs)
    if merged is not None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return [(f"yandex_labels_sorted_{ts}.pdf", merged)], warnings
    if not label_files:
        return [], warnings
    if len(pdfs) > 1:
        warnings.append(
            "Не удалось объединить PDF в один файл (установите pypdf). "
            "Файлы в ZIP идут по одному на заказ."
        )
    return label_files, warnings


def _export_list_to_google_sheet(
    *,
    spreadsheet_url: str,
    credentials_path: str,
    sheet_title: str,
    list_rows: list[YandexFbsListRow],
    template_sheet_name: str,
) -> str:
    data = [(r.sku, r.quantity, r.order_id) for r in list_rows]
    return write_fbs_list_from_template(
        spreadsheet_url,
        credentials_path,
        sheet_title,
        data,
        template_sheet_name=template_sheet_name,
        highlight_style="yandex_last4_digits",
    )


def fetch_awaiting_assembly_labels(
    adapter: YandexMarketAdapter,
    *,
    substatus: str = YANDEX_AWAITING_ASSEMBLY_SUBSTATUS,
    fbs_list_sheet_url: str = "",
    google_service_account_file: str = "",
    fbs_list_template_sheet: str = "FBSTemplate",
    yandex_label_format: str = "A9_HORIZONTALLY",
    yandex_label_rotate_degrees: int = 0,
) -> YandexAwaitingAssemblyBundle:
    """Список по артикулу, этикетки в том же порядке, лист в Google Таблице."""
    orders = adapter.list_awaiting_assembly_orders(substatus=substatus)
    if not orders:
        return YandexAwaitingAssemblyBundle()

    list_rows = build_sorted_list_rows(orders)
    ids_ordered = order_ids_in_list_order(list_rows)
    sheet_title = fbs_list_sheet_title()

    label_files, warnings = _fetch_labels_in_order(
        adapter,
        ids_ordered,
        label_format=yandex_label_format,
        label_rotate_degrees=yandex_label_rotate_degrees,
    )

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

    orders_by_id = {o.order_id: o for o in orders}
    ordered_orders = [orders_by_id[oid] for oid in ids_ordered if oid in orders_by_id]

    return YandexAwaitingAssemblyBundle(
        orders=ordered_orders,
        list_rows=list_rows,
        label_files=label_files,
        warnings=warnings,
        sheet_title=sheet_title,
        sheet_url=sheet_url,
    )


def format_orders_summary(
    orders: list[YandexFbsOrder] | None = None,
    *,
    list_rows: list[YandexFbsListRow] | None = None,
    max_lines: int = 40,
) -> str:
    if list_rows is not None:
        lines = [
            f"{r.seq}. {r.sku}×{r.quantity} — {r.order_id}"
            for r in list_rows[:max_lines]
        ]
        total = len(list_rows)
    else:
        orders = orders or []
        lines = []
        for order in orders[:max_lines]:
            items = ", ".join(f"{sku}×{qty}" for sku, qty in order.lines)
            lines.append(f"{order.order_id} ({order.substatus}): {items}")
        total = len(orders)
    if total > max_lines:
        lines.append(f"… ещё строк: {total - max_lines}")
    return "\n".join(lines)
