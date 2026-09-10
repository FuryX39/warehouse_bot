"""HTTP API WMS: заказы покупателей, отгрузки, инвентаризация."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.catalog_repository import CatalogRepository
from app.storage_warehouse_repository import InsufficientBinStockError, StorageWarehouseRepository
from app.warehouse_inventory_counts_repository import WarehouseInventoryCountsRepository
from app.crm_repository import CrmRepository
from app.warehouse_orders_repository import ORDERS_LIST_PAGE_SIZE, WarehouseOrdersRepository
from app.warehouse_shipments_repository import WarehouseShipmentsRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_wms_routes(
    app,
    orders_repo: WarehouseOrdersRepository,
    shipments_repo: WarehouseShipmentsRepository,
    inventory_counts_repo: WarehouseInventoryCountsRepository,
    catalog_repo: CatalogRepository,
    storage_repo: StorageWarehouseRepository,
    users_repo,
    packing_repo,
    require_warehouse_user,
    crm_repo: CrmRepository | None = None,
) -> None:
    @app.get("/api/warehouse/orders/meta")
    async def api_orders_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        assignees = users_repo.list_assignee_picker() if users_repo is not None else []
        return {
            "warehouses": storage_repo.warehouses_with_bins(),
            "assignees": assignees,
            "counterparties": crm_repo.list_counterparty_picker() if crm_repo else [],
            "statuses": [
                {"id": "open", "name": "Открыт"},
                {"id": "in_wave", "name": "В волне"},
                {"id": "packed", "name": "Упакован"},
                {"id": "shipped", "name": "Отгружен"},
                {"id": "cancelled", "name": "Отменён"},
            ],
        }

    @app.get("/api/warehouse/orders")
    async def api_orders_list(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        params = request.query_params
        counterparty_raw = str(params.get("counterparty_id") or "").strip()
        counterparty_id = int(counterparty_raw) if counterparty_raw.isdigit() else None
        source = str(params.get("source") or "").strip() or None
        status = str(params.get("status") or "").strip() or None
        q = str(params.get("q") or "")
        try:
            page = int(params.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        page = max(1, page)
        limit = ORDERS_LIST_PAGE_SIZE
        total = orders_repo.count_orders(
            source=source,
            counterparty_id=counterparty_id,
            status=status,
            q=q,
        )
        pages = max(1, (total + limit - 1) // limit) if total else 1
        if page > pages:
            page = pages
        offset = (page - 1) * limit
        rows = orders_repo.list_orders(
            source=source,
            counterparty_id=counterparty_id,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )
        return {
            "orders": [orders_repo.to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages,
        }

    @app.get("/api/warehouse/orders/{order_id}")
    async def api_orders_get(
        order_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = orders_repo.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return {"order": orders_repo.to_dict(row)}

    @app.get("/api/warehouse/shipments")
    async def api_shipments_list(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = shipments_repo.list_shipments()
        return {"shipments": [shipments_repo.to_dict(r) for r in rows]}

    @app.get("/api/warehouse/shipments/{shipment_id}")
    async def api_shipments_get(
        shipment_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = shipments_repo.get_shipment(shipment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Отгрузка не найдена")
        return {"shipment": shipments_repo.to_dict(row)}

    @app.post("/api/warehouse/shipments")
    async def api_shipments_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        payload = body if isinstance(body, dict) else {}
        post = bool(payload.get("post"))
        title = str(payload.get("title") or "").strip()
        try:
            if payload.get("packing_job_id"):
                row = shipments_repo.create_from_packing_job(
                    int(payload.get("packing_job_id")),
                    title=title,
                    post=post,
                )
            else:
                raw_ids = payload.get("order_ids") or []
                if not isinstance(raw_ids, list):
                    raise ValueError("order_ids должен быть массивом")
                order_ids = [int(x) for x in raw_ids]
                row = shipments_repo.create_draft(
                    order_ids,
                    title=title,
                    origin=str(payload.get("origin") or "manual"),
                    post=post,
                )
        except InsufficientBinStockError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"shipment": shipments_repo.to_dict(row)}

    @app.post("/api/warehouse/shipments/{shipment_id}/post")
    async def api_shipments_post(
        shipment_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = shipments_repo.post_shipment(shipment_id)
        except InsufficientBinStockError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"shipment": shipments_repo.to_dict(row)}

    @app.get("/api/warehouse/inventory-counts/meta")
    async def api_inventory_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"warehouses": storage_repo.warehouses_with_bins()}

    @app.get("/api/warehouse/inventory-counts")
    async def api_inventory_list(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = inventory_counts_repo.list_counts()
        return {"counts": [inventory_counts_repo.to_dict(r) for r in rows]}

    @app.get("/api/warehouse/inventory-counts/fill")
    async def api_inventory_fill(
        warehouse_id: int,
        bin_id: int | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            resolved = storage_repo.resolve_bin_id(int(warehouse_id), bin_id)
            items = inventory_counts_repo.fill_from_bin(int(warehouse_id), int(resolved))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items}

    @app.get("/api/warehouse/inventory-counts/products/search")
    async def api_inventory_search(
        q: str = "",
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"products": catalog_repo.list_products_picker(q=q)}

    @app.get("/api/warehouse/inventory-counts/{count_id}")
    async def api_inventory_get(
        count_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = inventory_counts_repo.get_count(count_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Пересчёт не найден")
        return {"count": inventory_counts_repo.to_dict(row)}

    @app.post("/api/warehouse/inventory-counts")
    async def api_inventory_create(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = inventory_counts_repo.save_draft(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"count": inventory_counts_repo.to_dict(row)}

    @app.put("/api/warehouse/inventory-counts/{count_id}")
    async def api_inventory_update(
        count_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = inventory_counts_repo.save_draft(body, count_id=count_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"count": inventory_counts_repo.to_dict(row)}

    @app.post("/api/warehouse/inventory-counts/{count_id}/post")
    async def api_inventory_post(
        count_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = inventory_counts_repo.post_count(count_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"count": inventory_counts_repo.to_dict(row)}

    @app.get("/api/warehouse/pick-waves")
    async def api_pick_waves_list(
        marketplace: str | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        names = {
            int(item["id"]): str(item["display_name"])
            for item in (users_repo.list_assignee_picker() if users_repo else [])
        }
        mp = str(marketplace or "").strip().lower() or None
        jobs = packing_repo.list_jobs(packer_names=names, marketplace=mp) if packing_repo else []
        return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}
