"""HTTP API списаний новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository


def register_warehouse_writeoffs_routes(
    app,
    writeoffs_repo: WarehouseWriteoffsRepository,
    catalog_repo: CatalogRepository,
    storage_repo: StorageWarehouseRepository,
    crm_repo: CrmRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/writeoffs/meta")
    async def api_writeoffs_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        warehouses = storage_repo.list_warehouses({})
        price_types = crm_repo.get_meta().get("price_types", [])
        return {
            "warehouses": [storage_repo.warehouse_to_dict(w) for w in warehouses],
            "price_types": price_types,
        }

    @app.get("/api/warehouse/writeoffs/products/search")
    async def api_writeoffs_search_products(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        params = request.query_params
        products = catalog_repo.list_products_picker(
            name=str(params.get("name") or "").strip(),
            sku=str(params.get("sku") or "").strip(),
            code=str(params.get("code") or "").strip(),
        )
        return {"products": products}

    @app.post("/api/warehouse/writeoffs/expand-kit")
    async def api_writeoffs_expand_kit(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            product_id = int(body.get("product_id"))
            quantity = int(body.get("quantity") or 1)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный товар или количество") from exc
        try:
            lines = catalog_repo.expand_kit_to_lines(product_id, quantity)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": lines}

    @app.post("/api/warehouse/writeoffs/price-by-type")
    async def api_writeoffs_price_by_type(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            price_type_id = int(body.get("price_type_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Выберите вид цены") from exc
        raw_ids = body.get("product_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(status_code=400, detail="product_ids обязателен")
        try:
            product_ids = [int(x) for x in raw_ids]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректные product_ids") from exc
        prices = catalog_repo.get_prices_for_products(product_ids, price_type_id)
        return {"prices": {str(k): v for k, v in prices.items()}}

    @app.get("/api/warehouse/writeoffs")
    async def api_writeoffs_list(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        rows = writeoffs_repo.list_writeoffs(filters)
        return {
            "writeoffs": [
                writeoffs_repo.writeoff_to_dict(r, include_items=False) for r in rows
            ]
        }

    @app.get("/api/warehouse/writeoffs/{writeoff_id}")
    async def api_writeoffs_get(
        writeoff_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = writeoffs_repo.get_writeoff(writeoff_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Списание не найдено")
        return {"writeoff": writeoffs_repo.writeoff_to_dict(row)}

    @app.post("/api/warehouse/writeoffs")
    async def api_writeoffs_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = writeoffs_repo.create_writeoff(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"writeoff": writeoffs_repo.writeoff_to_dict(row)}

    @app.put("/api/warehouse/writeoffs/{writeoff_id}")
    async def api_writeoffs_update(
        writeoff_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = writeoffs_repo.update_writeoff(writeoff_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Списание не найдено")
        return {"writeoff": writeoffs_repo.writeoff_to_dict(row)}

    @app.delete("/api/warehouse/writeoffs/{writeoff_id}")
    async def api_writeoffs_delete(
        writeoff_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        if not writeoffs_repo.delete_writeoff(writeoff_id):
            raise HTTPException(status_code=404, detail="Списание не найдено")
        return {"ok": True}


def _filters_from_query(params: Any) -> dict[str, str]:
    keys = ("q", "title", "comment", "warehouse_id")
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out
