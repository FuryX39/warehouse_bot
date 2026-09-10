"""HTTP API перемещений по ячейкам."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.catalog_repository import CatalogRepository
from app.storage_warehouse_repository import InsufficientBinStockError, StorageWarehouseRepository
from app.warehouse_bin_transfers_repository import WarehouseBinTransfersRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_bin_transfers_routes(
    app,
    bin_transfers_repo: WarehouseBinTransfersRepository,
    catalog_repo: CatalogRepository,
    storage_repo: StorageWarehouseRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/bin-transfers/meta")
    async def api_bin_transfers_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"warehouses": storage_repo.warehouses_with_bins()}

    @app.get("/api/warehouse/bin-transfers/products/search")
    async def api_bin_transfers_search(
        q: str = "",
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        products = catalog_repo.list_products_picker(q=q)
        return {"products": products}

    @app.get("/api/warehouse/bin-transfers")
    async def api_bin_transfers_list(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = bin_transfers_repo.list_transfers()
        return {"transfers": [bin_transfers_repo.to_dict(r, include_items=False) for r in rows]}

    @app.post("/api/warehouse/bin-transfers")
    async def api_bin_transfers_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = bin_transfers_repo.create_transfer(body)
        except InsufficientBinStockError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"transfer": bin_transfers_repo.to_dict(row)}

    @app.get("/api/warehouse/bin-transfers/{transfer_id}")
    async def api_bin_transfers_get(
        transfer_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = bin_transfers_repo.get_transfer(transfer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Документ не найден")
        return {"transfer": bin_transfers_repo.to_dict(row)}
