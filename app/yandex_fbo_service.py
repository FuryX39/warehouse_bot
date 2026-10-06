"""Сборка задания FBO YM из FBY API и каталога."""

from __future__ import annotations

from app.catalog_repository import CatalogRepository
from app.yandex_fbo_api import YandexFboApi, parse_cargo_units_pdf
from app.yandex_fbo_repository import YandexFboJobRow, YandexFboRepository


def create_yandex_fbo_job(
    *,
    api: YandexFboApi,
    catalog: CatalogRepository,
    packing_repo: YandexFboRepository,
    request_id: int,
    packer_user_ids: list[int],
    created_by_user_id: int | None,
) -> YandexFboJobRow:
    supply = api.get_supply(int(request_id))
    if not supply.get("is_child"):
        raise ValueError("Нужна дочерняя заявка FBY, не родительская ВРЦ")
    existing = packing_repo.find_active_by_request_id(int(request_id))
    if existing is not None:
        raise ValueError(
            f"Задание для заявки {request_id} уже создано (#{existing.id})"
        )
    items = api.get_items(int(request_id))
    if not items:
        raise ValueError("Товары поставки ещё недоступны")
    pdf = api.download_cargo_units_pdf(int(request_id))
    pages = parse_cargo_units_pdf(pdf)
    sku_map = catalog.lookup_products_by_skus([item["sku"] for item in items])
    products = []
    for item in items:
        sku = str(item["sku"]).strip()
        catalog_row = sku_map.get(sku.casefold())
        catalog_id = int(catalog_row["id"]) if catalog_row else None
        name = str(item.get("name") or "") or (
            str(catalog_row["name"]) if catalog_row else sku
        )
        scan_keys = [sku, *list(item.get("barcodes") or [])]
        if catalog_id:
            product = catalog.get_product(catalog_id)
            if product is not None:
                if product.name:
                    name = product.name
                for barcode in product.barcodes or []:
                    value = str(barcode.get("barcode") or "").strip()
                    if value:
                        scan_keys.append(value)
                for box in product.boxes or []:
                    value = str(box.get("barcode") or "").strip()
                    if value:
                        scan_keys.append(value)
        products.append(
            {
                "sku": sku,
                "product_name": name,
                "planned_qty": int(item["planned_qty"]),
                "catalog_product_id": catalog_id,
                "scan_keys": list(dict.fromkeys(key for key in scan_keys if key)),
            }
        )
    cargoes = [
        {"cargo_code": page.cargo_code, "page_index": page.page_index} for page in pages
    ]
    return packing_repo.create_job(
        request_id=int(supply["request_id"]),
        marketplace_request_id=str(supply.get("marketplace_request_id") or ""),
        warehouse_request_id=str(supply.get("warehouse_request_id") or ""),
        parent_request_id=supply.get("parent_request_id"),
        vrc_label=str(supply.get("vrc_label") or ""),
        warehouse_name=str(supply.get("warehouse_name") or ""),
        transit_warehouse=str(supply.get("transit_warehouse") or ""),
        transit_at=str(supply.get("transit_at") or ""),
        accept_at=str(supply.get("accept_at") or ""),
        supply_status=str(supply.get("status") or ""),
        packer_user_ids=packer_user_ids,
        created_by_user_id=created_by_user_id,
        products=products,
        cargoes=cargoes,
        cargo_labels_pdf=pdf,
    )
