"""HTTP API каталога товаров для новой панели /warehouse."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.catalog_bulk_import import build_import_template, import_products_from_xlsx
from app.catalog_barcode_import import build_barcode_import_template, import_barcodes_from_xlsx
from app.catalog_price_type_import import (
    build_price_type_prices_template,
    import_price_type_prices_from_xlsx,
)
from app.barcode_label_pdf import generate_barcode_label_pdf
from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.warehouse_stock_repository import WarehouseStockRepository
from app.warehouse_users_repository import WarehouseUserRow

_IMPORT_MAX_BYTES = 10 * 1024 * 1024


def register_warehouse_catalog_routes(
    app,
    catalog_repo: CatalogRepository,
    require_warehouse_user,
    stock_repo: WarehouseStockRepository | None = None,
    crm_repo: CrmRepository | None = None,
) -> None:
    @app.get("/api/warehouse/catalog/meta")
    async def api_catalog_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        meta = catalog_repo.get_meta()
        if crm_repo is not None:
            meta["price_types"] = crm_repo.get_meta().get("price_types", [])
        return meta

    @app.put("/api/warehouse/catalog/groups")
    async def api_catalog_save_groups(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        try:
            groups = catalog_repo.save_groups(items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"groups": groups}

    @app.put("/api/warehouse/catalog/units")
    async def api_catalog_save_units(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"units": catalog_repo.save_units(items)}

    @app.put("/api/warehouse/catalog/marking-types")
    async def api_catalog_save_marking_types(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"marking_types": catalog_repo.save_marking_types(items)}

    @app.get("/api/warehouse/catalog/price-types/{price_type_id}/products")
    async def api_catalog_price_type_products(
        price_type_id: int,
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        try:
            products = catalog_repo.list_products_for_price_type(price_type_id, filters)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"products": products}

    @app.put("/api/warehouse/catalog/price-types/{price_type_id}/prices")
    async def api_catalog_price_type_save_prices(
        price_type_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        try:
            updated = catalog_repo.save_prices_for_price_type(price_type_id, items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"updated": updated}

    @app.get("/api/warehouse/catalog/price-types/import/template")
    async def api_catalog_price_type_import_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            content = await asyncio.to_thread(build_price_type_prices_template, catalog_repo)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="price_type_prices_template.xlsx"',
            },
        )

    @app.post("/api/warehouse/catalog/price-types/import", response_model=None)
    async def api_catalog_price_type_import(
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ):
        if crm_repo is None:
            raise HTTPException(status_code=500, detail="CRM не подключён")
        data = await file.read()
        if len(data) > _IMPORT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        filename = (file.filename or "").lower()
        if not filename.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Нужен файл Excel в формате .xlsx")
        try:
            result = await asyncio.to_thread(
                import_price_type_prices_from_xlsx, catalog_repo, crm_repo, data
            )
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "ok": result.failed == 0,
            "price_type_id": result.price_type_id,
            "price_type_name": result.price_type_name,
            "created_price_type": result.created_price_type,
            "updated": result.updated,
            "failed": result.failed,
            "total_rows": result.total_rows,
            "error_report_b64": (
                base64.b64encode(result.error_report).decode("ascii")
                if result.error_report
                else None
            ),
        }

    @app.get("/api/warehouse/catalog/products")
    async def api_catalog_list_products(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        rows = catalog_repo.list_products(filters)
        return {
            "products": [catalog_repo.product_to_dict(r, include_details=False) for r in rows]
        }

    @app.get("/api/warehouse/catalog/products/picker")
    async def api_catalog_products_picker(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        q = (request.query_params.get("q") or "").strip()
        exclude_raw = request.query_params.get("exclude_id")
        exclude_id = None
        if exclude_raw:
            try:
                exclude_id = int(exclude_raw)
            except ValueError:
                exclude_id = None
        return {"products": catalog_repo.list_products_picker(q=q, exclude_id=exclude_id)}

    @app.get("/api/warehouse/catalog/products/import/template")
    async def api_catalog_import_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            content = await asyncio.to_thread(
                build_import_template, catalog_repo.get_meta()
            )
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="catalog_products_template.xlsx"',
            },
        )

    @app.post("/api/warehouse/catalog/products/import", response_model=None)
    async def api_catalog_import_products(
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ):
        data = await file.read()
        if len(data) > _IMPORT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        filename = (file.filename or "").lower()
        if not filename.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Нужен файл Excel в формате .xlsx")
        try:
            result = await asyncio.to_thread(import_products_from_xlsx, catalog_repo, data)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result.error_report:
            return Response(
                content=result.error_report,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": 'attachment; filename="catalog_import_errors.xlsx"',
                    "X-Import-Created": str(result.created),
                    "X-Import-Failed": str(result.failed),
                    "X-Import-Total": str(result.total_rows),
                },
            )
        if stock_repo is not None and result.created:
            stock_repo.rebuild_all()
        return {"ok": True, "created": result.created, "total_rows": result.total_rows}

    @app.get("/api/warehouse/catalog/barcodes/import/template")
    async def api_catalog_barcodes_import_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            content = await asyncio.to_thread(build_barcode_import_template, catalog_repo)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="catalog_barcodes_template.xlsx"',
            },
        )

    @app.post("/api/warehouse/catalog/barcodes/import", response_model=None)
    async def api_catalog_barcodes_import(
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ):
        data = await file.read()
        if len(data) > _IMPORT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        filename = (file.filename or "").lower()
        if not filename.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Нужен файл Excel в формате .xlsx")
        try:
            result = await asyncio.to_thread(import_barcodes_from_xlsx, catalog_repo, data)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result.error_report:
            return Response(
                content=result.error_report,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": 'attachment; filename="catalog_barcodes_import_errors.xlsx"',
                    "X-Import-Created": str(result.created),
                    "X-Import-Updated": str(result.updated),
                    "X-Import-Failed": str(result.failed),
                    "X-Import-Total": str(result.total_rows),
                },
            )
        return {
            "ok": True,
            "created": result.created,
            "updated": result.updated,
            "total_rows": result.total_rows,
        }

    @app.get("/api/warehouse/catalog/products/next-code")
    async def api_catalog_next_product_code(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"code": catalog_repo.generate_next_product_code()}

    @app.post("/api/warehouse/catalog/products/bulk-delete")
    async def api_catalog_bulk_delete_products(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        raw_ids = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(status_code=400, detail="Укажите ids — массив идентификаторов товаров")
        try:
            ids = [int(x) for x in raw_ids]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный список ids") from exc
        result = catalog_repo.delete_products(ids)
        if stock_repo is not None and result.get("deleted_skus"):
            for sku in result["deleted_skus"]:
                stock_repo.remove_cached_sku(sku)
            stock_repo.recalculate_skus(set(result["deleted_skus"]))
        return {
            "ok": True,
            "deleted": int(result.get("deleted") or 0),
            "failed": result.get("failed") or [],
        }

    @app.get("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_get_product(
        product_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = catalog_repo.get_product(product_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return {"product": catalog_repo.product_to_dict(row)}

    @app.get("/api/warehouse/catalog/products/{product_id}/barcode-label")
    async def api_catalog_barcode_label(
        product_id: int,
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ):
        barcode = str(request.query_params.get("barcode") or "").strip()
        if not barcode:
            raise HTTPException(status_code=400, detail="Укажите barcode")
        row = catalog_repo.get_product(product_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        allowed: set[str] = set()
        for item in row.barcodes or []:
            if isinstance(item, dict):
                code = str(item.get("barcode") or "").strip()
            else:
                code = str(item or "").strip()
            if code:
                allowed.add(code)
        if barcode not in allowed:
            raise HTTPException(status_code=404, detail="Штрихкод не привязан к этому товару")
        try:
            pdf = await asyncio.to_thread(
                generate_barcode_label_pdf,
                barcode,
                sku=str(row.sku or ""),
                product_name=str(row.name or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        safe_bc = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in barcode)[:40]
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="barcode_{safe_bc}.pdf"'},
        )

    @app.post("/api/warehouse/catalog/products")
    async def api_catalog_create_product(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = catalog_repo.create_product(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if stock_repo is not None and row.sku:
            stock_repo.recalculate_skus({row.sku})
        return {"product": catalog_repo.product_to_dict(row)}

    @app.put("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_update_product(
        product_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        old = catalog_repo.get_product(product_id)
        old_sku = str(old.sku).strip() if old and old.sku else ""
        try:
            row = catalog_repo.update_product(product_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        if stock_repo is not None:
            skus = {s for s in (old_sku, str(row.sku or "").strip()) if s}
            if skus:
                stock_repo.recalculate_skus(skus)
        return {"product": catalog_repo.product_to_dict(row)}

    @app.delete("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_delete_product(
        product_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        old = catalog_repo.get_product(product_id)
        old_sku = str(old.sku).strip() if old and old.sku else ""
        try:
            deleted = catalog_repo.delete_product(product_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Товар не найден")
        if stock_repo is not None and old_sku:
            stock_repo.remove_cached_sku(old_sku)
            stock_repo.recalculate_skus({old_sku})
        return {"ok": True}


def _filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "kind",
        "group_id",
        "unit_id",
        "marking_type_id",
        "name",
        "sku",
        "code",
        "external_code",
        "country",
        "description",
        "weight",
        "volume",
        "barcode",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out
