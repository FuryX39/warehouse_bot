"""Админские маршруты новой панели /warehouse."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.env_admin import read_env_fields, write_env_fields
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_admin_routes(app, require_warehouse_admin) -> None:
    @app.get("/api/warehouse/admin/env")
    async def api_admin_env(
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        return {
            "fields": read_env_fields(),
            "restart_required_note": "После сохранения перезапустите веб-сервер и бота, чтобы все настройки применились.",
        }

    @app.put("/api/warehouse/admin/env")
    async def api_admin_save_env(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_admin),
    ) -> dict:
        fields = body.get("fields")
        if not isinstance(fields, list):
            raise HTTPException(status_code=400, detail="fields должен быть массивом")
        try:
            write_env_fields(fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "fields": read_env_fields(),
            "restart_required": True,
        }
