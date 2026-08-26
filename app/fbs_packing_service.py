"""Создание задания FBS-упаковки Яндекс и сопоставление с каталогом."""

from __future__ import annotations

from typing import Any

from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.fbs_labels_common import merge_label_pdfs
from app.fbs_packing_repository import FbsPackingJobRow, FbsPackingRepository, MARKETPLACE_YANDEX
from app.google_sheet_write import fbs_list_sheet_title
from app.yandex_fbs_labels import (
    YandexFbsListRow,
    _export_list_to_google_sheet,
    build_order_box_labels,
    collect_yandex_unit_labels,
    load_yandex_fbs_list_rows,
    normalize_yandex_fbs_substatus,
)
from app.adapters.yandex_market import YandexMarketAdapter


def _bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def resolve_catalog_products(
    catalog: CatalogRepository, skus: list[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_sku, _by_code, _by_barcode = catalog.build_product_import_index()
    found: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    seen: set[str] = set()
    for sku in skus:
        key = str(sku or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        product = by_sku.get(key)
        if product is None:
            missing.append(str(sku).strip())
            continue
        found[key] = product
    return found, missing


def lookup_scan_product(
    catalog: CatalogRepository, raw: str
) -> tuple[str, int | None]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Пустой штрихкод")
    by_sku, by_code, by_barcode = catalog.build_product_import_index()
    key = text.casefold()
    product = by_barcode.get(key) or by_sku.get(key) or by_code.get(key)
    if product is None:
        raise ValueError("Штрихкод не найден в каталоге")
    return str(product.get("sku") or ""), int(product["id"]) if product.get("id") else None


def create_yandex_packing_job(
    *,
    adapter: YandexMarketAdapter,
    catalog: CatalogRepository,
    packing_repo: FbsPackingRepository,
    settings: Settings,
    order_substatus: str,
    build_list: bool,
    item_limit: int | None,
    packer_user_ids: list[int],
    created_by_user_id: int | None,
) -> FbsPackingJobRow:
    substatus = normalize_yandex_fbs_substatus(order_substatus)
    list_rows, selected_orders, warnings, _available = load_yandex_fbs_list_rows(
        adapter,
        substatus=substatus,
        default_stocks_sheet_url=settings.default_stocks_sheet_url,
        google_service_account_file=settings.google_service_account_file,
        fbs_assembly_sheet_name=settings.fbs_assembly_sheet_name,
        assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
        max_units=item_limit,
        apply_assembly=bool(build_list),
    )
    if not list_rows:
        raise ValueError("Нет заказов для задания")

    units, label_warnings = collect_yandex_unit_labels(
        adapter,
        selected_orders,
        list_rows,
        substatus=substatus,
        label_format=settings.yandex_label_format,
        label_rotate_degrees=settings.yandex_label_rotate_degrees,
    )
    warnings.extend(label_warnings)
    units_with_pdf = [unit for unit in units if unit.pdf]
    if not units_with_pdf:
        detail = "; ".join(warnings[:5]) if warnings else "нет PDF"
        raise ValueError(f"Не удалось получить ярлыки: {detail}")

    catalog_by_sku, missing = resolve_catalog_products(
        catalog, [unit.sku for unit in units_with_pdf]
    )
    for sku in missing:
        warnings.append(f"Артикул «{sku}» не найден в каталоге — строка всё равно в задании")

    sheet_url = ""
    sheet_title = fbs_list_sheet_title() if build_list else ""
    merged_pdf: bytes | None = None
    if build_list:
        pdfs = [unit.pdf for unit in units_with_pdf if unit.pdf]
        merged_pdf = merge_label_pdfs(pdfs)
        if merged_pdf is None and len(pdfs) == 1:
            merged_pdf = pdfs[0]
        sheet_url_cfg = (settings.fbs_list_sheet_url or "").strip()
        creds_path = (settings.google_service_account_file or "").strip()
        job_rows = [
            YandexFbsListRow(
                seq=index,
                order_id=unit.order_id,
                sku=unit.sku,
                quantity=1,
                status=substatus,
            )
            for index, unit in enumerate(units_with_pdf, start=1)
        ]
        if sheet_url_cfg and creds_path and job_rows:
            try:
                sheet_url = _export_list_to_google_sheet(
                    spreadsheet_url=sheet_url_cfg,
                    credentials_path=creds_path,
                    sheet_title=sheet_title,
                    list_rows=job_rows,
                    orders=selected_orders,
                    template_sheet_name=(settings.fbs_list_template_sheet or "FBSTemplate").strip(),
                ) or ""
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Google Таблица: {exc}")
        elif sheet_url_cfg and job_rows and not creds_path:
            warnings.append(
                "Google Таблица: задайте GOOGLE_SERVICE_ACCOUNT_FILE "
                "(JSON service account с доступом к FBS_LIST_SHEET_URL)."
            )
        elif not sheet_url_cfg and job_rows:
            warnings.append(
                "Google Таблица: задайте FBS_LIST_SHEET_URL (ссылка на таблицу для FBS-списков)."
            )

    line_payloads = []
    for seq, unit in enumerate(units_with_pdf, start=1):
        product = catalog_by_sku.get(unit.sku.casefold())
        line_payloads.append(
            {
                "seq": seq,
                "sku": unit.sku,
                "product_id": int(product["id"]) if product else None,
                "product_name": str(product["name"]) if product else "",
                "order_id": unit.order_id,
                "box_id": unit.box_id,
                "place_index": unit.place_index,
                "place_total": unit.place_total,
                "scan_keys": unit.scan_keys(),
                "pdf": unit.pdf,
            }
        )
    return packing_repo.create_job(
        marketplace=MARKETPLACE_YANDEX,
        order_substatus=substatus,
        build_list=bool(build_list),
        created_by_user_id=created_by_user_id,
        packer_user_ids=packer_user_ids,
        sheet_url=sheet_url,
        sheet_title=sheet_title,
        warnings=warnings,
        merged_pdf=merged_pdf,
        lines=line_payloads,
    )


def list_rows_payload(list_rows: list[YandexFbsListRow], orders) -> list[dict[str, Any]]:
    displays = build_order_box_labels(list_rows, orders)
    return [
        {
            "seq": row.seq,
            "sku": row.sku,
            "quantity": row.quantity,
            "order_id": row.order_id,
            "order_display": display,
            "posting_number": row.order_id,
        }
        for row, display in zip(list_rows, displays)
    ]
