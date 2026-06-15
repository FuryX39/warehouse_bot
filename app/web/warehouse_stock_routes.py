"""HTTP API остатков новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.warehouse_stock_repository import WarehouseStockRepository
from app.warehouse_users_repository import WarehouseUserRow


def _product_filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "name",
        "sku",
        "code",
        "group_id",
        "kind",
        "warehouse_id",
        "only_nonzero",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out


def _warehouse_filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "warehouse_name",
        "warehouse_code",
        "warehouse_group_id",
        "warehouse_id",
        "hide_empty",
        "name",
        "sku",
        "code",
        "group_id",
        "kind",
        "only_nonzero",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out


def register_warehouse_stock_routes(
    app,
    stock_repo: WarehouseStockRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/stock/meta")
    async def api_stock_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return stock_repo.get_meta()

    @app.get("/api/warehouse/stock/products")
    async def api_stock_by_products(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _product_filters_from_query(request.query_params)
        rows = stock_repo.list_by_products(filters)
        return {"items": [stock_repo.balance_to_dict(r) for r in rows]}

    @app.get("/api/warehouse/stock/warehouses")
    async def api_stock_by_warehouses(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _warehouse_filters_from_query(request.query_params)
        return {"warehouses": stock_repo.list_by_warehouses(filters)}

    @app.get("/api/warehouse/stock/breakdown/{sku}")
    async def api_stock_breakdown(
        sku: str,
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        metric = str(request.query_params.get("metric") or "full").strip().lower()
        try:
            return stock_repo.breakdown(sku, metric)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
