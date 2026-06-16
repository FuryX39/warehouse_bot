"""HTTP API перемещений новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_transfers_repository import WarehouseTransfersRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_transfers_routes(
    app,
    transfers_repo: WarehouseTransfersRepository,
    catalog_repo: CatalogRepository,
    storage_repo: StorageWarehouseRepository,
    crm_repo: CrmRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/transfers/meta")
    async def api_transfers_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        warehouses = storage_repo.list_warehouses({})
        price_types = crm_repo.get_meta().get("price_types", [])
        return {
            "warehouses": [storage_repo.warehouse_to_dict(w) for w in warehouses],
            "price_types": price_types,
        }

    @app.get("/api/warehouse/transfers/products/search")
    async def api_transfers_search_products(
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

    @app.post("/api/warehouse/transfers/expand-kit")
    async def api_transfers_expand_kit(
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

    @app.post("/api/warehouse/transfers/price-by-type")
    async def api_transfers_price_by_type(
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

    @app.get("/api/warehouse/transfers")
    async def api_transfers_list(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        rows = transfers_repo.list_transfers(filters)
        return {
            "transfers": [
                transfers_repo.transfer_to_dict(r, include_items=False) for r in rows
            ]
        }

    @app.get("/api/warehouse/transfers/{transfer_id}")
    async def api_transfers_get(
        transfer_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = transfers_repo.get_transfer(transfer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        return {"transfer": transfers_repo.transfer_to_dict(row)}

    @app.post("/api/warehouse/transfers")
    async def api_transfers_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = transfers_repo.create_transfer(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"transfer": transfers_repo.transfer_to_dict(row)}

    @app.put("/api/warehouse/transfers/{transfer_id}")
    async def api_transfers_update(
        transfer_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = transfers_repo.update_transfer(transfer_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        return {"transfer": transfers_repo.transfer_to_dict(row)}

    @app.delete("/api/warehouse/transfers/{transfer_id}")
    async def api_transfers_delete(
        transfer_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        if not transfers_repo.delete_transfer(transfer_id):
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        return {"ok": True}


def _filters_from_query(params: Any) -> dict[str, str]:
    keys = ("q", "title", "comment", "from_warehouse_id", "to_warehouse_id", "warehouse_id")
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out
