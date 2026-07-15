"""Заказы Yandex Market «готовы к сборке» и отдельные PDF-этикетки FBS."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from app.adapters.yandex_market import (
    YANDEX_AWAITING_ASSEMBLY_SUBSTATUS,
    YandexFbsOrder,
    YandexMarketAdapter,
)
from app.fbs_assembly_order import apply_assembly_order_to_yandex_rows
from app.fbs_labels_common import merge_label_pdfs
from app.google_sheet_write import fbs_list_sheet_title, write_fbs_list_from_template
from app.ozon_label_pdf import normalize_ozon_package_label_pdf

if TYPE_CHECKING:
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
    available_units: int = 0


def get_configured_yandex_adapter(coordinator: StockCoordinator) -> YandexMarketAdapter | None:
    for adapter in coordinator.adapters:
        if isinstance(adapter, YandexMarketAdapter) and adapter.is_configured():
            return adapter
    return None


def build_sorted_list_rows(orders: list[YandexFbsOrder]) -> list[YandexFbsListRow]:
    """Строки списка: одна строка на каждую физическую товарную единицу."""
    flat = []
    for order in orders:
        for sku, quantity in order.lines:
            flat.extend(
                (sku, order.order_id, 1, order.substatus)
                for _ in range(max(0, int(quantity)))
            )
    flat.sort(key=lambda row: row[0].casefold())
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
    orders: list[YandexFbsOrder],
    list_rows: list[YandexFbsListRow],
    *,
    label_format: str = "A9_HORIZONTALLY",
    label_rotate_degrees: int = 0,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    box_ids_by_order_sku: dict[tuple[str, str], deque[int]] = defaultdict(deque)
    for order in orders:
        box_ids = adapter.set_order_unit_boxes(order)
        box_index = 0
        for item in order.items:
            key = (order.order_id, item.sku)
            for _ in range(item.quantity):
                box_ids_by_order_sku[key].append(box_ids[box_index])
                box_index += 1

    label_files: list[tuple[str, bytes]] = []
    warnings: list[str] = []
    for row in list_rows:
        key = (row.order_id, row.sku)
        if not box_ids_by_order_sku[key]:
            warnings.append(
                f"Заказ {row.order_id}, {row.sku}: не найдена отдельная коробка для этикетки"
            )
            continue
        box_id = box_ids_by_order_sku[key].popleft()
        try:
            pdf = adapter.fetch_box_label_pdf(
                row.order_id,
                box_id,
                label_format=label_format,
            )
            if label_rotate_degrees:
                pdf = normalize_ozon_package_label_pdf(
                    pdf, rotate_degrees=label_rotate_degrees
                )
            label_files.append((f"yandex_label_{row.order_id}_{box_id}.pdf", pdf))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Заказ {row.order_id}, коробка {box_id}: {exc}")

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
    orders: list[YandexFbsOrder],
    template_sheet_name: str,
) -> str:
    display_numbers = build_order_box_labels(list_rows, orders)
    data = [
        (row.sku, row.quantity, display_number)
        for row, display_number in zip(list_rows, display_numbers)
    ]
    return write_fbs_list_from_template(
        spreadsheet_url,
        credentials_path,
        sheet_title,
        data,
        template_sheet_name=template_sheet_name,
        highlight_style="yandex_last4_digits",
    )


def build_order_box_labels(
    list_rows: list[YandexFbsListRow],
    orders: list[YandexFbsOrder],
) -> list[str]:
    """Номер заказа с номером коробки: 123456 1/2, 123456 2/2."""
    totals = {
        order.order_id: sum(max(0, int(quantity)) for _, quantity in order.lines)
        for order in orders
    }
    positions_by_order_sku: dict[tuple[str, str], deque[int]] = defaultdict(deque)
    for order in orders:
        position = 1
        for item in order.items:
            for _ in range(max(0, int(item.quantity))):
                positions_by_order_sku[(order.order_id, item.sku)].append(position)
                position += 1

    fallback_seen: dict[str, int] = defaultdict(int)
    out: list[str] = []
    for row in list_rows:
        total = max(1, totals.get(row.order_id, 1))
        positions = positions_by_order_sku[(row.order_id, row.sku)]
        if positions:
            position = positions.popleft()
            fallback_seen[row.order_id] = max(fallback_seen[row.order_id], position)
        else:
            fallback_seen[row.order_id] += 1
            position = fallback_seen[row.order_id]
        suffix = f" {position}/{total}" if total > 1 else ""
        out.append(f"{row.order_id}{suffix}")
    return out


def fetch_awaiting_assembly_labels(
    adapter: YandexMarketAdapter,
    *,
    substatus: str = YANDEX_AWAITING_ASSEMBLY_SUBSTATUS,
    fbs_list_sheet_url: str = "",
    google_service_account_file: str = "",
    fbs_list_template_sheet: str = "FBSTemplate",
    yandex_label_format: str = "A9_HORIZONTALLY",
    yandex_label_rotate_degrees: int = 0,
    default_stocks_sheet_url: str = "",
    fbs_assembly_sheet_name: str = "assembly",
    assembly_sheet_gid: int | None = None,
    max_units: int | None = None,
) -> YandexAwaitingAssemblyBundle:
    """Все STARTED-заказы: по единице в коробке, порядок списка и ярлыков — assembly."""
    orders = adapter.list_awaiting_assembly_orders(substatus=substatus)
    if not orders:
        return YandexAwaitingAssemblyBundle()

    list_rows = build_sorted_list_rows(orders)
    list_rows, assembly_warnings = apply_assembly_order_to_yandex_rows(
        list_rows,
        default_stocks_sheet_url=default_stocks_sheet_url,
        google_service_account_file=google_service_account_file,
        assembly_sheet_name=fbs_assembly_sheet_name,
        assembly_sheet_gid=assembly_sheet_gid,
        row_factory=YandexFbsListRow,
    )
    available_units = len(list_rows)
    if max_units is not None:
        if int(max_units) <= 0:
            raise ValueError("Количество товаров должно быть больше нуля")
        list_rows = list_rows[: int(max_units)]
    ids_ordered = order_ids_in_list_order(list_rows)
    orders_by_id = {order.order_id: order for order in orders}
    selected_orders = [
        orders_by_id[order_id] for order_id in ids_ordered if order_id in orders_by_id
    ]
    sheet_title = fbs_list_sheet_title()

    label_files, warnings = _fetch_labels_in_order(
        adapter,
        selected_orders,
        list_rows,
        label_format=yandex_label_format,
        label_rotate_degrees=yandex_label_rotate_degrees,
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
                orders=selected_orders,
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

    return YandexAwaitingAssemblyBundle(
        orders=selected_orders,
        list_rows=list_rows,
        label_files=label_files,
        warnings=warnings,
        sheet_title=sheet_title,
        sheet_url=sheet_url,
        available_units=available_units,
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
