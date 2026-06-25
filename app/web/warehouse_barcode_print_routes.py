"""Прокси к локальному агенту печати штрихкодов (когда браузер не может достучаться до 127.0.0.1)."""

from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException

from app.barcode_print_agent_client import (
    barcode_print_agent_health,
    barcode_print_agent_host,
    barcode_print_agent_port,
    barcode_print_agent_print,
)
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_barcode_print_routes(app, require_warehouse_user) -> None:
    @app.get("/api/warehouse/barcode-print/health")
    async def api_barcode_print_health(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return await asyncio.to_thread(barcode_print_agent_health)

    @app.get("/api/warehouse/barcode-print/config")
    async def api_barcode_print_config(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"host": barcode_print_agent_host(), "port": barcode_print_agent_port()}

    @app.post("/api/warehouse/barcode-print/print")
    async def api_barcode_print_print(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        barcode = str(body.get("barcode") or "").strip()
        if not barcode:
            raise HTTPException(status_code=400, detail="Укажите barcode")
        try:
            copies = int(body.get("copies") or 1)
        except (TypeError, ValueError):
            copies = 1
        copies = max(1, min(9999, copies))
        payload = {
            "barcode": barcode,
            "sku": str(body.get("sku") or "").strip(),
            "name": str(body.get("name") or "").strip(),
            "copies": copies,
        }
        result = await asyncio.to_thread(barcode_print_agent_print, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error") or "Агент печати недоступен")
        return result
