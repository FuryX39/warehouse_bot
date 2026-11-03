"""Создание и работа задания FBO WB new по Excel кабинета. В WB ничего не отправляется."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogProduct, CatalogRepository
from app.fbs_packing_service import CIS_REQUIRED_ERROR, resolve_packing_scan
from app.marking.cis import parse_cis, remap_jcuken_to_qwerty
from app.shelf_life import expiry_from_production
from app.wb_fbo_packing_service import fbo_nonstandard_box_qty_warning
from app.wb_fbo_sheet_repository import (
    WbFboSheetBoxRow,
    WbFboSheetJobRow,
    WbFboSheetPalletRow,
    WbFboSheetRepository,
)
from app.wb_fbo_sheet_pallet_pdf import generate_wb_fbo_sheet_pallet_labels_pdf
from app.wb_fbo_sheet_xlsx import digits_only, parse_boxes_xlsx, parse_goods_xlsx
from app.wb_fbw_supply_qr import extract_wb_supply_data_from_pdf
from app.wb_fbw_box_label_pdf import (
    format_fbw_box_human_id,
    generate_wb_fbw_box_labels_pdf,
    parse_fbw_box_id_from_package_code,
)


def create_wb_fbo_sheet_job(
    *,
    catalog: CatalogRepository,
    packing_repo: WbFboSheetRepository,
    goods_xlsx: bytes,
    boxes_xlsx: bytes,
    supply_qr_pdf: bytes,
    packer_user_ids: list[int],
    created_by_user_id: int | None,
    supply_id: str = "",
    warehouse_name: str = "",
    seller_name: str = "",
    plan_date: str = "",
    box_type: str = "Короб",
    transfer_number: str = "",
) -> WbFboSheetJobRow:
    if not supply_qr_pdf or not supply_qr_pdf.startswith(b"%PDF"):
        raise ValueError("Прикрепите PDF с QR поставки из кабинета WB")
    supply_data = extract_wb_supply_data_from_pdf(supply_qr_pdf)
    goods = parse_goods_xlsx(goods_xlsx)
    boxes = parse_boxes_xlsx(boxes_xlsx)
    products: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in goods:
        found = catalog.find_product_by_barcode(item.barcode)
        if found is None and item.sku:
            with Session(catalog.engine) as session:
                row = session.scalar(
                    select(CatalogProduct).where(CatalogProduct.sku == item.sku)
                )
                if row is not None:
                    found = catalog.get_product(int(row.id))
        if found is None:
            warnings.append(f"Баркод «{item.barcode}» не найден в каталоге")
        products.append(
            {
                "barcode": item.barcode,
                "sku": item.sku or (found.sku if found else ""),
                "name": (found.name if found else "") or item.name,
                "product_id": int(found.id) if found else None,
                "qty_plan": item.qty,
            }
        )
    resolved_supply_id = supply_data.supply_id or str(supply_id or "").strip()
    resolved_warehouse = supply_data.warehouse_name or str(warehouse_name or "").strip()
    resolved_seller = supply_data.seller_name or str(seller_name or "").strip()
    resolved_date = supply_data.plan_date or str(plan_date or "").strip()
    resolved_type = str(supply_data.supply_type or "").strip()
    if not resolved_supply_id:
        raise ValueError("В QR поставки нет номера поставки")
    if not resolved_warehouse:
        raise ValueError("В QR поставки нет склада назначения")
    if not resolved_seller:
        raise ValueError("В QR поставки нет наименования юридического лица")
    if not resolved_date:
        raise ValueError("В QR поставки нет даты поставки")
    if not resolved_type:
        raise ValueError("В QR поставки нет типа упаковки")
    return packing_repo.create_job(
        supply_id=resolved_supply_id,
        warehouse_name=resolved_warehouse,
        seller_name=resolved_seller,
        plan_date=resolved_date,
        box_type=str(box_type or "Короб").strip() or "Короб",
        packer_user_ids=packer_user_ids,
        created_by_user_id=created_by_user_id,
        warnings=warnings,
        goods_xlsx=goods_xlsx,
        boxes_xlsx=boxes_xlsx,
        supply_qr_pdf=supply_qr_pdf,
        supply_qr_code=supply_data.qr_code,
        supply_type=resolved_type,
        transfer_number=str(transfer_number or "").strip()[:128],
        source_pallet_count=supply_data.pallet_count,
        products=products,
        boxes=_boxes_for_job(boxes),
    )


def _scan_keys_for_box(box: WbFboSheetBoxRow) -> list[str]:
    keys = [
        box.box_human_id,
        format_fbw_box_human_id(box.box_human_id),
        digits_only(box.box_human_id),
        box.package_code,
        parse_fbw_box_id_from_package_code(box.package_code),
        format_fbw_box_human_id(parse_fbw_box_id_from_package_code(box.package_code)),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        text = str(raw or "").strip()
        if not text:
            continue
        fold = text.casefold()
        if fold in seen:
            continue
        seen.add(fold)
        out.append(text)
    return out


def find_box_by_scan(job: WbFboSheetJobRow, barcode: str) -> WbFboSheetBoxRow | None:
    key = str(barcode or "").strip()
    if not key:
        return None
    fold = key.casefold()
    digits = digits_only(key)
    for box in job.boxes:
        keys = {item.casefold() for item in _scan_keys_for_box(box)}
        if fold in keys:
            return box
        if digits and digits == digits_only(box.box_human_id):
            return box
    return None


def _catalog_box_qty(product, barcode: str) -> int | None:
    key = str(barcode or "").strip().casefold()
    for item in product.boxes or []:
        if str(item.get("barcode") or "").strip().casefold() == key:
            try:
                qty = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                return None
            return qty if qty > 0 else None
    return None


def find_pallet_by_scan(job: WbFboSheetJobRow, barcode: str) -> WbFboSheetPalletRow | None:
    key = str(barcode or "").strip()
    if not key:
        return None
    fold = key.casefold()
    for pallet in job.pallets:
        if str(pallet.pallet_human_id or "").casefold() == fold:
            return pallet
    return None


def _job_product_by_catalog(
    job: WbFboSheetJobRow,
    product_id: int | None,
    sku: str = "",
):
    if product_id:
        pid = int(product_id)
        for item in job.products:
            if item.product_id and int(item.product_id) == pid:
                return item
    sku_key = str(sku or "").strip().casefold()
    if sku_key:
        for item in job.products:
            if str(item.sku or "").casefold() == sku_key:
                return item
    return None


def _find_job_goods_by_code(job: WbFboSheetJobRow, text: str, catalog: CatalogRepository):
    fold = text.casefold()
    goods = None
    for item in job.products:
        if item.barcode.casefold() == fold or item.sku.casefold() == fold:
            goods = item
            break
    suggested_qty = None
    catalog_product = catalog.find_product_by_barcode(text)
    if catalog_product is not None:
        suggested_qty = _catalog_box_qty(catalog_product, text)
        if goods is None:
            barcodes = {str(b.get("barcode") or "").casefold() for b in catalog_product.barcodes}
            barcodes.add(str(catalog_product.sku or "").casefold())
            for item in job.products:
                if item.product_id and int(item.product_id) == int(catalog_product.id):
                    goods = item
                    break
                if item.barcode.casefold() in barcodes or item.sku.casefold() in barcodes:
                    goods = item
                    break
                if item.sku and item.sku.casefold() == str(catalog_product.sku or "").casefold():
                    goods = item
                    break
    return goods, suggested_qty


def _product_resolve_payload(
    catalog: CatalogRepository,
    packing_repo: WbFboSheetRepository,
    goods,
    *,
    suggested_qty: int | None,
    cis_raw: str = "",
) -> dict[str, Any]:
    product_payload = packing_repo.product_to_dict(goods)
    attach_sheet_images(catalog, {"products": [product_payload]})
    has_cis = bool(str(cis_raw or "").strip())
    product_payload["has_cis"] = has_cis
    return {
        "kind": "product",
        "product": product_payload,
        "suggested_qty": suggested_qty,
        "remaining": max(0, goods.qty_plan - goods.qty_assigned),
        "has_cis": has_cis,
        "cis": str(cis_raw or "").strip(),
    }


def collect_sheet_cis_codes(payload: dict[str, Any] | None) -> list[str]:
    data = payload or {}
    raw = data.get("cis_list")
    if raw is None:
        raw = data.get("cis_codes")
    items: list[str] = []
    if isinstance(raw, list):
        items = [str(item or "").strip() for item in raw]
    else:
        text = str(data.get("cis") or data.get("cis_raw") or "").strip()
        if text:
            items = [text]
    return [item for item in items if item]


def require_cis_for_sheet_product(
    catalog: CatalogRepository,
    product,
    cis_codes: list[str] | str,
    *,
    quantity: int,
) -> list[str]:
    product_id = product.product_id if product is not None else None
    if not catalog.product_requires_cis(product_id):
        return []
    if isinstance(cis_codes, str):
        codes = [cis_codes.strip()] if cis_codes.strip() else []
    else:
        codes = [str(item or "").strip() for item in (cis_codes or []) if str(item or "").strip()]
    if not codes:
        raise ValueError(CIS_REQUIRED_ERROR)
    if len(codes) != int(quantity):
        raise ValueError(
            f"Количество ({quantity}) должно совпадать с числом пропиканных КИЗ ({len(codes)})"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        text = remap_jcuken_to_qwerty(raw)
        resolved = resolve_packing_scan(catalog, text)
        if not resolved.is_cis:
            raise ValueError(CIS_REQUIRED_ERROR)
        if product_id and resolved.product_id and int(product_id) != int(resolved.product_id):
            raise ValueError("КИЗ не относится к выбранному товару")
        key = resolved.cis_key or text
        if key in seen:
            raise ValueError("Этот КИЗ уже пропикан")
        seen.add(key)
        normalized.append(resolved.cis_raw or text)
    return normalized


def resolve_sheet_scan(
    catalog: CatalogRepository,
    packing_repo: WbFboSheetRepository,
    job_id: int,
    barcode: str,
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    text = remap_jcuken_to_qwerty(str(barcode or "").strip())
    if not text:
        raise ValueError("Пустой штрихкод")
    job = packing_repo.get_job(job_id, include_lines=True)
    if job is None:
        raise ValueError("Задание не найдено")
    box = find_box_by_scan(job, text)
    if box is not None:
        return {"kind": "wb_box", "box": packing_repo.box_to_dict(box)}
    cis_record = parse_cis(text)
    if cis_record.ok:
        packing_resolved = resolve_packing_scan(catalog, text)
        goods = _job_product_by_catalog(job, packing_resolved.product_id, packing_resolved.sku)
        if goods is None:
            raise ValueError("Товар есть в базе, в этой поставке его нет")
        return _product_resolve_payload(
            catalog,
            packing_repo,
            goods,
            suggested_qty=None,
            cis_raw=packing_resolved.cis_raw or text,
        )
    goods, suggested_qty = _find_job_goods_by_code(job, text, catalog)
    if goods is None:
        raise ValueError("Штрихкод не найден в задании")
    if catalog.product_requires_cis(goods.product_id):
        raise ValueError(CIS_REQUIRED_ERROR)
    return _product_resolve_payload(
        catalog,
        packing_repo,
        goods,
        suggested_qty=suggested_qty,
    )


def _boxes_for_job(boxes) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in boxes:
        key = digits_only(item.box_id) or item.box_id.casefold()
        if key not in grouped:
            grouped[key] = {
                "box_id": item.box_id,
                "package_code": item.package_code,
                "items": [],
            }
            order.append(key)
        if item.product_barcode and int(item.qty or 0) > 0:
            grouped[key]["items"].append(
                {
                    "product_barcode": item.product_barcode,
                    "qty": int(item.qty),
                    "expiry": str(item.expiry or ""),
                }
            )
    return [grouped[key] for key in order]


def label_row_for_box(job: WbFboSheetJobRow, box: WbFboSheetBoxRow) -> dict[str, Any]:
    return {
        "supply_id": job.supply_id,
        "package_code": box.package_code,
        "box_id": box.box_human_id or parse_fbw_box_id_from_package_code(box.package_code),
        "quantity": int(box.item_qty or 0),
        "plan_date": job.plan_date,
        "warehouse": job.warehouse_name,
        "seller": job.seller_name,
        "box_type": job.box_type or "Короб",
        "sku": box.sku,
    }


def pdf_for_boxes(job: WbFboSheetJobRow, boxes: list[WbFboSheetBoxRow]) -> bytes:
    return generate_wb_fbw_box_labels_pdf([label_row_for_box(job, box) for box in boxes])


def pdf_for_pallets(job: WbFboSheetJobRow, pallets: list[WbFboSheetPalletRow]) -> bytes:
    return generate_wb_fbo_sheet_pallet_labels_pdf(
        [
            {
                "pallet_id": item.pallet_human_id,
                "seq": item.seq,
                "supply_id": job.supply_id,
            }
            for item in pallets
        ]
    )


def qty_warning_for_box(
    catalog: CatalogRepository,
    job: WbFboSheetJobRow,
    box: WbFboSheetBoxRow,
    *,
    product_barcode: str = "",
    item_qty: int | None = None,
) -> str:
    barcode = str(product_barcode or "").strip()
    qty = item_qty
    if not barcode and box.items:
        barcode = str(box.items[-1].product_barcode or "")
        qty = int(box.items[-1].item_qty or 0) if qty is None else qty
    if not barcode:
        barcode = str(box.product_barcode or "").split(",")[0].strip()
    product = next(
        (
            item
            for item in job.products
            if item.barcode.casefold() == barcode.casefold()
        ),
        None,
    )
    if qty is None:
        match = next(
            (
                item
                for item in box.items
                if item.product_barcode.casefold() == barcode.casefold()
            ),
            None,
        )
        qty = int(match.item_qty) if match else int(box.item_qty or 0)
    pid = product.product_id if product else box.product_id
    standard: set[int] = set()
    if pid:
        standard = catalog.product_box_quantities_by_id([int(pid)]).get(int(pid), set())
    return fbo_nonstandard_box_qty_warning(
        standard,
        item_qty=int(qty or 0),
        box_number=box.box_human_id or str(box.seq),
    )


def expiry_for_assignment(
    catalog: CatalogRepository,
    *,
    product_id: int | None,
    production_date: object,
    existing_expiry: str = "",
) -> str:
    if not product_id:
        return ""
    has_life, years = catalog.shelf_life_by_product_ids([int(product_id)]).get(
        int(product_id), (False, 0)
    )
    if not has_life:
        return ""
    text = str(production_date or "").strip()
    if text:
        return expiry_from_production(text, years=years)
    kept = str(existing_expiry or "").strip()
    if kept:
        return kept
    raise ValueError("Укажите дату производства")


def assigned_expiry_for_barcode(job: WbFboSheetJobRow, product_barcode: str) -> str:
    key = str(product_barcode or "").strip().casefold()
    if not key:
        return ""
    for box in job.boxes:
        for item in box.items:
            if str(item.product_barcode or "").casefold() != key:
                continue
            text = str(item.expiry or "").strip()
            if text:
                return text
    return ""


def attach_sheet_images(catalog: CatalogRepository, payload: dict[str, Any]) -> None:
    buckets: list[dict[str, Any]] = []
    for key in ("products", "remaining_groups", "boxes"):
        rows = payload.get(key)
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict):
                    continue
                buckets.append(item)
                nested = item.get("items")
                if isinstance(nested, list):
                    buckets.extend(row for row in nested if isinstance(row, dict))
    pids = [int(item["product_id"]) for item in buckets if item.get("product_id")]
    urls = catalog.image_urls_by_product_ids(pids) if pids else {}
    shelf = catalog.shelf_life_by_product_ids(pids) if pids else {}
    cis_flags = catalog.products_require_cis(pids) if pids else {}
    any_required = False
    for item in buckets:
        pid = item.get("product_id")
        required = bool(cis_flags.get(int(pid))) if pid else False
        item["require_cis"] = required
        if required:
            any_required = True
        if not pid:
            item.setdefault("has_shelf_life", False)
            item.setdefault("shelf_life_years", 0)
            continue
        item["image_url"] = urls.get(int(pid), "") or item.get("image_url") or ""
        has_life, years = shelf.get(int(pid), (False, 0))
        item["has_shelf_life"] = bool(has_life)
        item["shelf_life_years"] = int(years) if has_life else 0
    payload["require_cis"] = any_required
