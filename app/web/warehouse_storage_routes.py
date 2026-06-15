"""HTTP API складов хранения для новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_storage_routes(
    app,
    storage_repo: StorageWarehouseRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/storage/meta")
    async def api_storage_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return storage_repo.get_meta()

    @app.put("/api/warehouse/storage/groups")
    async def api_storage_save_groups(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"groups": storage_repo.save_groups(items)}

    @app.get("/api/warehouse/storage/warehouses")
    async def api_storage_list_warehouses(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        rows = storage_repo.list_warehouses(filters)
        return {"warehouses": [storage_repo.warehouse_to_dict(r) for r in rows]}

    @app.get("/api/warehouse/storage/warehouses/{warehouse_id}")
    async def api_storage_get_warehouse(
        warehouse_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = storage_repo.get_warehouse(warehouse_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Склад не найден")
        return {"warehouse": storage_repo.warehouse_to_dict(row)}

    @app.post("/api/warehouse/storage/warehouses")
    async def api_storage_create_warehouse(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = storage_repo.create_warehouse(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"warehouse": storage_repo.warehouse_to_dict(row)}

    @app.put("/api/warehouse/storage/warehouses/{warehouse_id}")
    async def api_storage_update_warehouse(
        warehouse_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = storage_repo.update_warehouse(warehouse_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Склад не найден")
        return {"warehouse": storage_repo.warehouse_to_dict(row)}

    @app.get("/api/warehouse/storage/warehouses/{warehouse_id}/stocks")
    async def api_storage_warehouse_stocks(
        warehouse_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        if storage_repo.get_warehouse(warehouse_id) is None:
            raise HTTPException(status_code=404, detail="Склад не найден")
        stocks = storage_repo.list_stocks_for_warehouse(warehouse_id)
        return {"stocks": [{"sku": sku, "stock": qty} for sku, qty in sorted(stocks.items())]}


def _filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "group_id",
        "name",
        "address",
        "address_comment",
        "comment",
        "code",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out
