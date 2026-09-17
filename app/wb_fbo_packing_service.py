"""Создание задания FBO WB: поставка из supplies-api, QR, листы паллет, этикетки коробов."""

from __future__ import annotations

from typing import Any

from app.adapters.wildberries import WildberriesAdapter, _parse_fbw_supply_id
from app.catalog_repository import CatalogRepository
from app.fbs_labels_common import split_pdf_into_pages
from app.fbs_packing_service import lookup_scan_product, resolve_catalog_products, resolve_packing_scan
from app.wb_fbo_packing_repository import WbFboPackingJobRow, WbFboPackingRepository
from app.wb_fbw_box_label_pdf import (
    build_fbw_box_label_rows,
    format_fbw_box_human_id,
    format_fbw_plan_date,
    generate_wb_fbw_box_labels_pdf,
)
from app.wb_fbw_pallet_sheets import (
    WbFbwPalletSheetData,
    fbw_sheet_city,
    generate_wb_fbw_pallet_sheets_pdf,
    parse_pallet_count,
)
from app.wb_fbw_supply_qr import extract_wb_supply_qr_code_from_pdf


def preview_wb_fbo_supply(adapter: WildberriesAdapter, supply_id: object) -> dict[str, Any]:
    sid = _parse_fbw_supply_id(supply_id)
    supply = adapter.fetch_fbw_supply(sid)
    goods = adapter.fetch_fbw_supply_goods(sid)
    boxes = adapter.fetch_fbw_supply_packages(sid)
    rows = build_fbw_box_label_rows(
        supply_id=int(sid),
        goods=goods,
        boxes=boxes,
        supply=supply,
    )
    warehouse = str(supply.get("warehouseName") or "").strip()
    city = fbw_sheet_city(warehouse)
    sku_qty: dict[str, int] = {}
    for row in rows:
        sku = str(row.get("sku") or "").strip() or "—"
        sku_qty[sku] = sku_qty.get(sku, 0) + 1
    return {
        "supply_id": sid,
        "warehouse_name": warehouse,
        "city": city,
        "seller_name": str(
            supply.get("sellerName") or supply.get("supplierAssignName") or ""
        ).strip(),
        "plan_date": format_fbw_plan_date(supply.get("supplyDate")),
        "box_count": len(rows),
        "sku_count": len([k for k in sku_qty if k != "—"]),
        "goods_count": len(goods),
        "skus": [{"sku": sku, "boxes": qty} for sku, qty in sku_qty.items()],
        "boxes": [
            {
                "box_id": row.get("box_id") or "",
                "box_human_id": format_fbw_box_human_id(row.get("box_id")),
                "sku": row.get("sku") or "",
                "quantity": row.get("quantity") or 0,
                "barcode": row.get("product_barcode") or "",
            }
            for row in rows
        ],
    }


def create_wb_fbo_packing_job(
    *,
    adapter: WildberriesAdapter,
    catalog: CatalogRepository,
    packing_repo: WbFboPackingRepository,
    supply_id: object,
    pallet_count: object,
    city: object,
    packer_user_ids: list[int],
    created_by_user_id: int | None,
    supply_qr_pdf: bytes,
) -> WbFboPackingJobRow:
    if not supply_qr_pdf or not supply_qr_pdf.startswith(b"%PDF"):
        raise ValueError("Прикрепите PDF с QR поставки из кабинета WB")
    sid = _parse_fbw_supply_id(supply_id)
    count = parse_pallet_count(pallet_count)
    supply = adapter.fetch_fbw_supply(sid)
    goods = adapter.fetch_fbw_supply_goods(sid)
    boxes = adapter.fetch_fbw_supply_packages(sid)
    label_rows = build_fbw_box_label_rows(
        supply_id=int(sid),
        goods=goods,
        boxes=boxes,
        supply=supply,
    )
    if not label_rows:
        raise ValueError("В поставке нет коробов")

    warehouse = str(supply.get("warehouseName") or "").strip()
    city_n = str(city or "").strip() or fbw_sheet_city(warehouse)
    if not city_n:
        raise ValueError("Укажите город для листов паллет")
    seller = str(supply.get("sellerName") or supply.get("supplierAssignName") or "").strip()
    plan_date = format_fbw_plan_date(supply.get("supplyDate"))
    qr_code = extract_wb_supply_qr_code_from_pdf(supply_qr_pdf)

    catalog_by_sku, missing = resolve_catalog_products(
        catalog, [str(row.get("sku") or "") for row in label_rows]
    )
    warnings = [f"Артикул «{sku}» не найден в каталоге — строка всё равно в задании" for sku in missing]
    if not qr_code:
        warnings.append("Из PDF не удалось прочитать WB-GI-… — файл сохранён, код можно не заполнять")

    merged_pdf = generate_wb_fbw_box_labels_pdf(label_rows)
    page_pdfs = split_pdf_into_pages(merged_pdf)
    if len(page_pdfs) != len(label_rows):
        page_pdfs = [generate_wb_fbw_box_labels_pdf([row]) for row in label_rows]

    _by_sku, _by_code, by_barcode = catalog.build_product_import_index()
    line_payloads: list[dict] = []
    for seq, (row, pdf) in enumerate(zip(label_rows, page_pdfs, strict=True), start=1):
        sku = str(row.get("sku") or "").strip()
        product = catalog_by_sku.get(sku.casefold()) if sku else None
        scan_keys = _scan_keys_for_row(row, product, by_barcode)
        line_payloads.append(
            {
                "seq": seq,
                "sku": sku,
                "product_id": int(product["id"]) if product and product.get("id") else None,
                "product_name": str(product["name"]) if product else "",
                "box_human_id": format_fbw_box_human_id(row.get("box_id")),
                "package_code": str(row.get("package_code") or ""),
                "item_qty": int(row.get("quantity") or 1),
                "scan_keys": scan_keys,
                "pdf": pdf,
            }
        )

    sheets_pdf = generate_wb_fbw_pallet_sheets_pdf(
        WbFbwPalletSheetData(supply_id=sid, city=city_n, pallet_count=count)
    )
    return packing_repo.create_job(
        supply_id=sid,
        warehouse_name=warehouse,
        city=city_n,
        seller_name=seller,
        plan_date=plan_date,
        pallet_count=count,
        supply_qr_code=qr_code,
        created_by_user_id=created_by_user_id,
        packer_user_ids=packer_user_ids,
        warnings=warnings,
        supply_qr_pdf=supply_qr_pdf,
        pallet_sheets_pdf=sheets_pdf,
        box_labels_pdf=merged_pdf,
        lines=line_payloads,
    )


def fbo_nonstandard_box_qty_warning(
    standard_qtys: set[int],
    *,
    item_qty: int,
    box_number: str,
) -> str:
    """Текст для упаковщика, если в коробе нестандартное число штук.

    Стандарт — количества из раздела «Короба» карточки товара.
    Если в карточке коробов нет, предупреждение не показываем.
    """
    if not standard_qtys:
        return ""
    try:
        qty = int(item_qty)
    except (TypeError, ValueError):
        return ""
    if qty in standard_qtys:
        return ""
    number = str(box_number or "").strip() or "?"
    return f"В грузоместе номер {number} находится {qty} товара"


def attach_fbo_box_qty_warnings(catalog: CatalogRepository, payload: dict[str, Any]) -> None:
    """Пишет ``qty_warning`` в строки задания FBO (не в remaining_groups)."""
    buckets: list[dict[str, Any]] = []
    for key in ("lines", "active_lines"):
        rows = payload.get(key)
        if isinstance(rows, list):
            buckets.extend(item for item in rows if isinstance(item, dict))
    active = payload.get("active_line")
    if isinstance(active, dict):
        buckets.append(active)
    pids = [int(item["product_id"]) for item in buckets if item.get("product_id")]
    qty_map = catalog.product_box_quantities_by_id(pids) if pids else {}
    for item in buckets:
        pid = item.get("product_id")
        standard = qty_map.get(int(pid), set()) if pid else set()
        box_number = str(
            item.get("box_id") or item.get("order_display") or item.get("seq") or ""
        ).strip()
        try:
            qty = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        item["qty_warning"] = fbo_nonstandard_box_qty_warning(
            standard,
            item_qty=qty,
            box_number=box_number,
        )


def resolve_fbo_scan(
    catalog: CatalogRepository,
    packing_repo: WbFboPackingRepository,
    job_id: int,
    barcode: str,
):
    text = str(barcode or "").strip()
    if not text:
        raise ValueError("Пустой штрихкод")
    try:
        return resolve_packing_scan(catalog, text)
    except ValueError:
        pass
    job = packing_repo.get_job(job_id, include_lines=True)
    if job is None:
        raise ValueError("Задание не найдено")
    key = text.casefold()
    for line in job.lines:
        if any(str(item).strip().casefold() == key for item in line.scan_keys):
            from app.fbs_packing_service import PackingScanResolve

            return PackingScanResolve(sku=line.sku, product_id=line.product_id)
    raise ValueError("Штрихкод не найден в задании")


def _scan_keys_for_row(
    row: dict[str, Any],
    product: dict[str, Any] | None,
    by_barcode: dict[str, dict[str, Any]],
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        fold = text.casefold()
        if fold in seen:
            return
        seen.add(fold)
        keys.append(text)

    add(row.get("product_barcode"))
    add(row.get("sku"))
    add(row.get("package_code"))
    pid = int(product["id"]) if product and product.get("id") else None
    if pid:
        for code, item in by_barcode.items():
            if item.get("id") == pid:
                add(code)
    return keys


def lookup_fbo_pick(catalog: CatalogRepository, sku: str, product_id: int | None) -> tuple[str, int | None]:
    if product_id:
        return str(sku or ""), int(product_id)
    text = str(sku or "").strip()
    if not text:
        raise ValueError("Нет артикула")
    try:
        return lookup_scan_product(catalog, text)
    except ValueError:
        return text, None
