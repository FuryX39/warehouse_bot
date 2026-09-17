"""Создание и работа задания FBO WB new по Excel кабинета. В WB ничего не отправляется."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogProduct, CatalogRepository
from app.shelf_life import expiry_from_production
from app.wb_fbo_packing_service import fbo_nonstandard_box_qty_warning
from app.wb_fbo_sheet_repository import (
    WbFboSheetBoxRow,
    WbFboSheetJobRow,
    WbFboSheetRepository,
)
from app.wb_fbo_sheet_xlsx import digits_only, parse_boxes_xlsx, parse_goods_xlsx
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
    packer_user_ids: list[int],
    created_by_user_id: int | None,
    supply_id: str = "",
    warehouse_name: str = "",
    seller_name: str = "",
    plan_date: str = "",
    box_type: str = "Короб",
) -> WbFboSheetJobRow:
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
    return packing_repo.create_job(
        supply_id=str(supply_id or "").strip(),
        warehouse_name=str(warehouse_name or "").strip(),
        seller_name=str(seller_name or "").strip(),
        plan_date=str(plan_date or "").strip(),
        box_type=str(box_type or "Короб").strip() or "Короб",
        packer_user_ids=packer_user_ids,
        created_by_user_id=created_by_user_id,
        warnings=warnings,
        goods_xlsx=goods_xlsx,
        boxes_xlsx=boxes_xlsx,
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


def resolve_sheet_scan(
    catalog: CatalogRepository,
    packing_repo: WbFboSheetRepository,
    job_id: int,
    barcode: str,
) -> dict[str, Any]:
    text = str(barcode or "").strip()
    if not text:
        raise ValueError("Пустой штрихкод")
    job = packing_repo.get_job(job_id, include_lines=True)
    if job is None:
        raise ValueError("Задание не найдено")
    box = find_box_by_scan(job, text)
    if box is not None:
        return {"kind": "wb_box", "box": packing_repo.box_to_dict(box)}
    goods = None
    fold = text.casefold()
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
    if goods is None:
        raise ValueError("Штрихкод не найден в задании")
    product_payload = packing_repo.product_to_dict(goods)
    attach_sheet_images(catalog, {"products": [product_payload]})
    return {
        "kind": "product",
        "product": product_payload,
        "suggested_qty": suggested_qty,
        "remaining": max(0, goods.qty_plan - goods.qty_assigned),
    }


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
    for item in buckets:
        pid = item.get("product_id")
        if not pid:
            item.setdefault("has_shelf_life", False)
            item.setdefault("shelf_life_years", 0)
            continue
        item["image_url"] = urls.get(int(pid), "") or item.get("image_url") or ""
        has_life, years = shelf.get(int(pid), (False, 0))
        item["has_shelf_life"] = bool(has_life)
        item["shelf_life_years"] = int(years) if has_life else 0
