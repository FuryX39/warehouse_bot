"""HTTP API формирования прайс-листов."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.vseinstrumenti_pricat import build_vseinstrumenti_pricat
from app.warehouse_users_repository import WarehouseUserRow

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 20 * 1024 * 1024


async def _xlsx(file: UploadFile, title: str) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail=f"{title}: нужен файл .xlsx")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{title}: файл пустой")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"{title}: файл больше 20 МБ")
    return data


def register_warehouse_price_lists_routes(app, require_warehouse_user) -> None:
    @app.post("/api/warehouse/products/price-lists/vseinstrumenti/pricat")
    async def api_vseinstrumenti_pricat(
        stock_file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        stock_data = await _xlsx(stock_file, "Выгрузка остатков")
        try:
            result, stats = await asyncio.to_thread(
                build_vseinstrumenti_pricat,
                stock_data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("vseinstrumenti PRICAT generation failed")
            raise HTTPException(status_code=500, detail="Ошибка формирования PRICAT") from exc
        return Response(
            content=result,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="PRICAT_result.xlsx"',
                "X-PRICAT-Stats": json.dumps(stats, ensure_ascii=True, separators=(",", ":")),
            },
        )
