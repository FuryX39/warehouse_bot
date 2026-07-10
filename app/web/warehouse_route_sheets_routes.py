"""HTTP API маршрутных листов маркетплейсов."""

from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from app.marketplace_route_sheets import (
    DEFAULT_ROUTE_STATUSES,
    DEFAULT_ROUTE_SUPPLIER,
    generate_vseinstrumenti_route_sheets_pdf,
    normalize_vseinstrumenti_route_sheet_payload,
)
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_route_sheets_routes(app, require_warehouse_user) -> None:
    @app.get("/api/warehouse/marketplaces/route-sheets/meta")
    async def api_route_sheets_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {
            "vseinstrumenti": {
                "supplier": DEFAULT_ROUTE_SUPPLIER,
                "statuses": list(DEFAULT_ROUTE_STATUSES),
            },
            "marketplaces": [
                {"id": "vseinstrumenti", "title": "ВсеИнструменты", "enabled": True},
                {"id": "ozon", "title": "Ozon", "enabled": False},
                {"id": "wildberries", "title": "Wildberries", "enabled": False},
                {"id": "yandex_market", "title": "Яндекс Маркет", "enabled": False},
            ],
        }

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
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="vseinstrumenti_route_sheets.pdf"',
            },
        )
