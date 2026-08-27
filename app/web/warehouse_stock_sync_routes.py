"""HTTP API синхронизации остатков с маркетплейсами (новая панель)."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Depends, Form, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import Settings
from app.repositories import (
    STOCK_SYNC_LAST_FAIL_TS_KEY,
    STOCK_SYNC_LAST_OK_TS_KEY,
    InventoryRepository,
)
from app.services import StockCoordinator
from app.sheet_import import import_stocks_from_google_sheet, import_tops_from_google_sheet
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_users_repository import WarehouseUserRow


class StockSyncSettingsBody(BaseModel):
    warehouse_id: int = Field(..., ge=1)


def register_warehouse_stock_sync_routes(
    app,
    *,
    settings: Settings,
    inventory_repo: InventoryRepository,
    storage_repo: StorageWarehouseRepository,
    coordinator: StockCoordinator,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/stock-sync/meta")
    async def api_stock_sync_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        warehouses = [
            storage_repo.warehouse_to_dict(row)
            for row in storage_repo.list_warehouses({})
        ]
        source_id = inventory_repo.get_sync_source_warehouse_id()
        source = None
        if source_id is not None:
            row = storage_repo.get_warehouse(int(source_id))
            if row is not None:
                source = storage_repo.warehouse_to_dict(row)
        return {
            "warehouses": warehouses,
            "source_warehouse_id": source_id,
            "source_warehouse": source,
            "stock_sync_enabled": bool(settings.stock_sync_enabled),
            "default_stocks_sheet_url": str(settings.default_stocks_sheet_url or ""),
        }

    @app.put("/api/warehouse/stock-sync/settings")
    async def api_stock_sync_settings(
        body: StockSyncSettingsBody,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            source_id = inventory_repo.set_sync_source_warehouse_id(int(body.warehouse_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = storage_repo.get_warehouse(int(source_id))
        return {
            "ok": True,
            "source_warehouse_id": source_id,
            "source_warehouse": storage_repo.warehouse_to_dict(row) if row else None,
        }

    @app.get("/api/warehouse/stock-sync/status")
    async def api_stock_sync_status(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        adapters = [
            {"name": a.name, "configured": bool(a.is_configured())} for a in coordinator.adapters
        ]
        last_run = coordinator.last_run_at.isoformat() if coordinator.last_run_at else None
        last_ok_ts = inventory_repo.get_sync_int(STOCK_SYNC_LAST_OK_TS_KEY)
        last_fail_ts = inventory_repo.get_sync_int(STOCK_SYNC_LAST_FAIL_TS_KEY)
        source_id = inventory_repo.get_sync_source_warehouse_id()
        source = None
        if source_id is not None:
            row = storage_repo.get_warehouse(int(source_id))
            if row is not None:
                source = storage_repo.warehouse_to_dict(row)
        return {
            "last_run_at": last_run,
            "last_ok_ts": last_ok_ts,
            "last_fail_ts": last_fail_ts,
            "last_error": coordinator.last_error,
            "last_warnings": list(coordinator.last_warnings or []),
            "adapters": adapters,
            "telegram_configured": bool(settings.telegram_bot_token),
            "stock_sync_enabled": bool(settings.stock_sync_enabled),
            "source_warehouse_id": source_id,
            "source_warehouse": source,
        }

    @app.post("/api/warehouse/stock-sync/run")
    async def api_stock_sync_run(
        mode: str = Form(default="auto"),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        mode_l = (mode or "auto").strip().lower()
        if mode_l not in ("auto", "delta", "full"):
            raise HTTPException(status_code=400, detail="mode: auto, delta или full")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: coordinator.sync_cycle(mode_l))

    @app.get("/api/warehouse/stock-sync/inventory")
    async def api_stock_sync_inventory(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = inventory_repo.get_inventory_snapshot()
        return {
            "items": [
                {
                    "sku": r.sku,
                    "name": r.name,
                    "image_url": r.image_url,
                    "stock": int(r.stock),
                    "reserve": int(r.reserve),
                    "available": int(r.available),
                    "is_top": bool(r.is_top),
                }
                for r in rows
            ],
            "source_warehouse_id": inventory_repo.get_sync_source_warehouse_id(),
        }

    @app.put("/api/warehouse/stock-sync/stock")
    async def api_stock_sync_put_stock(
        sku: Annotated[str, Form()],
        stock: int = Form(),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        if stock < 0:
            raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным")
        inventory_repo.upsert_stock(sku_n, int(stock))
        return {"sku": sku_n, "stock": int(stock)}

    @app.delete("/api/warehouse/stock-sync/stock")
    async def api_stock_sync_delete_stock(
        sku: Annotated[str, Query()],
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        if not inventory_repo.delete_stock_by_sku(sku_n):
            raise HTTPException(status_code=404, detail="Остаток для этого артикула не найден")
        return {"sku": sku_n, "deleted": True}

    @app.put("/api/warehouse/stock-sync/top-flag")
    async def api_stock_sync_top_flag(
        sku: Annotated[str, Form()],
        is_top: bool = Form(),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        try:
            return inventory_repo.set_top_flag_for_sku(sku_n, bool(is_top))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/warehouse/stock-sync/missing-tops")
    async def api_stock_sync_missing_tops(
        threshold: Annotated[int, Query(description="Порог доступного остатка")] = 5,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        if threshold < 0:
            raise HTTPException(status_code=400, detail="threshold должен быть >= 0")
        rows = inventory_repo.get_missing_top_items(int(threshold))
        return {
            "threshold": int(threshold),
            "count": len(rows),
            "items": [
                {
                    "sku": r.sku,
                    "name": r.name,
                    "image_url": r.image_url,
                    "stock": int(r.stock),
                    "reserve": int(r.reserve),
                    "available": int(r.available),
                    "is_top": bool(r.is_top),
                }
                for r in rows
            ],
        }

    @app.post("/api/warehouse/stock-sync/import-sheet")
    async def api_stock_sync_import_sheet(
        url: Annotated[str, Form()] = "",
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        sheet_url = (url or "").strip() or str(settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL Google Sheets или задайте DEFAULT_STOCKS_SHEET_URL",
            )

        def _run() -> dict:
            stocks_by_sku, warnings = import_stocks_from_google_sheet(sheet_url)
            if not stocks_by_sku:
                return {
                    "updated": 0,
                    "sku_in_sheet": 0,
                    "warnings": warnings,
                    "message": "Импорт завершён: в таблице не найдено валидных строк.",
                }
            updated = inventory_repo.upsert_stocks(stocks_by_sku)
            return {
                "updated": updated,
                "sku_in_sheet": len(stocks_by_sku),
                "warnings": warnings[:40],
                "warnings_more": max(0, len(warnings) - 40),
            }

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка импорта: {exc}") from exc

    @app.post("/api/warehouse/stock-sync/import-tops")
    async def api_stock_sync_import_tops(
        url: Annotated[str, Form()] = "",
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        sheet_url = (url or "").strip() or str(settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL Google Sheets или задайте DEFAULT_STOCKS_SHEET_URL",
            )

        def _run() -> dict:
            top_skus, warnings = import_tops_from_google_sheet(sheet_url)
            result = inventory_repo.set_top_flags_by_skus(top_skus)
            return {
                **result,
                "warnings": warnings[:40],
                "warnings_more": max(0, len(warnings) - 40),
            }

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка импорта топов: {exc}") from exc
