"""HTTP API каталога товаров для новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.catalog_repository import CatalogRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_catalog_routes(
    app,
    catalog_repo: CatalogRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/catalog/meta")
    async def api_catalog_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return catalog_repo.get_meta()

    @app.put("/api/warehouse/catalog/groups")
    async def api_catalog_save_groups(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"groups": catalog_repo.save_groups(items)}

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

    @app.get("/api/warehouse/catalog/products/next-code")
    async def api_catalog_next_product_code(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"code": catalog_repo.generate_next_product_code()}

    @app.get("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_get_product(
        product_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = catalog_repo.get_product(product_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return {"product": catalog_repo.product_to_dict(row)}

    @app.post("/api/warehouse/catalog/products")
    async def api_catalog_create_product(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = catalog_repo.create_product(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"product": catalog_repo.product_to_dict(row)}

    @app.put("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_update_product(
        product_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = catalog_repo.update_product(product_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return {"product": catalog_repo.product_to_dict(row)}

    @app.delete("/api/warehouse/catalog/products/{product_id}")
    async def api_catalog_delete_product(
        product_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            deleted = catalog_repo.delete_product(product_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Товар не найден")
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
