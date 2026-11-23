"""HTTP API бидера WB Продвижение."""

from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException, Query

from app.config import Settings
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_promotion import (
    WbPromotionClient,
    WbPromotionError,
    get_campaign,
    list_campaigns,
    parse_days,
    parse_status_preset,
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WbPromotionError):
        code = 400
        text = str(exc)
        if "не найден" in text.lower():
            code = 404
        elif "нет доступа" in text.lower():
            code = 403
        elif "не задан" in text.lower():
            code = 400
        return HTTPException(status_code=code, detail=text)
    return HTTPException(status_code=400, detail=str(exc))


def register_warehouse_bidder_routes(app, settings: Settings, require_warehouse_user) -> None:
    def _client() -> WbPromotionClient:
        return WbPromotionClient(settings.wb_api_token)

    @app.get("/api/warehouse/marketplaces/bidder/campaigns")
    async def api_bidder_campaigns(
        status: str = Query("active"),
        days: int = Query(7),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            parse_status_preset(status)
            parse_days(days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            return await asyncio.to_thread(
                list_campaigns, _client(), status=status, days=days
            )
        except (WbPromotionError, ValueError) as exc:
            raise _http_error(exc) from exc

    @app.get("/api/warehouse/marketplaces/bidder/campaigns/{advert_id}")
    async def api_bidder_campaign(
        advert_id: int,
        days: int = Query(30),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            parse_days(days, default=30)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            return await asyncio.to_thread(
                get_campaign, _client(), advert_id, days=days
            )
        except (WbPromotionError, ValueError) as exc:
            raise _http_error(exc) from exc
