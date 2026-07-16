"""HTTP API маршрутных листов маркетплейсов."""

from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from app.marketplace_route_sheets import (
    DEFAULT_ROUTE_SUPPLIER,
    ROUTE_SHEET_CARGO_TYPES,
    generate_vseinstrumenti_route_sheets_pdf,
    list_route_purchase_statuses,
    normalize_vseinstrumenti_route_sheet_payload,
    route_sheet_content_disposition,
    route_sheet_download_filename,
    save_route_purchase_statuses,
)
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_route_sheets_routes(app, require_warehouse_user) -> None:
    @app.get("/api/warehouse/marketplaces/route-sheets/meta")
    async def api_route_sheets_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        statuses = await asyncio.to_thread(list_route_purchase_statuses)
        return {
            "vseinstrumenti": {
                "supplier": DEFAULT_ROUTE_SUPPLIER,
                "statuses": [row["name"] for row in statuses],
                "purchase_statuses": statuses,
                "cargo_types": [
                    {"id": cargo_id, "title": cargo_meta["title"]}
                    for cargo_id, cargo_meta in ROUTE_SHEET_CARGO_TYPES.items()
                ],
            },
            "marketplaces": [
                {"id": "vseinstrumenti", "title": "ВсеИнструменты", "enabled": True},
                {"id": "ozon", "title": "Ozon", "enabled": False},
                {"id": "wildberries", "title": "Wildberries", "enabled": False},
                {"id": "yandex_market", "title": "Яндекс Маркет", "enabled": False},
            ],
        }

    @app.get("/api/warehouse/marketplaces/route-sheets/purchase-statuses")
    async def api_route_sheets_purchase_statuses(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = await asyncio.to_thread(list_route_purchase_statuses)
        return {"purchase_statuses": rows}

    @app.put("/api/warehouse/marketplaces/route-sheets/purchase-statuses")
    async def api_route_sheets_save_purchase_statuses(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        saved = await asyncio.to_thread(save_route_purchase_statuses, items)
        return {"ok": True, "purchase_statuses": saved}

    @app.post("/api/warehouse/marketplaces/route-sheets/vseinstrumenti.pdf")
    async def api_route_sheets_vseinstrumenti_pdf(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            payload = normalize_vseinstrumenti_route_sheet_payload(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        content = await asyncio.to_thread(generate_vseinstrumenti_route_sheets_pdf, payload)
        filename = route_sheet_download_filename("vseinstrumenti", payload.purchase_number)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": route_sheet_content_disposition(filename),
            },
        )
