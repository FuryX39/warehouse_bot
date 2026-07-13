"""HTTP API репрайсера Яндекс Маркет."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.crm_repository import CrmRepository
from app.catalog_repository import CatalogRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.yandex_repricer import process_yandex_prices_workbook

_IMPORT_MAX_BYTES = 10 * 1024 * 1024
_PREVIEW_MAX_ROWS = 100

logger = logging.getLogger(__name__)


def _header_json(value: object) -> str:
    """JSON для HTTP-заголовка: только ASCII (latin-1), иначе Starlette отдаёт 500."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def register_warehouse_repricer_routes(
    app,
    catalog_repo: CatalogRepository,
    crm_repo: CrmRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/marketplaces/repricer/meta")
    async def api_repricer_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        meta = await asyncio.to_thread(crm_repo.get_meta)
        return {
            "price_types": meta.get("price_types") or [],
        }

    @app.post("/api/warehouse/marketplaces/repricer/calculate")
    async def api_repricer_calculate(
        price_type_id: int = Form(...),
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            pt_id = int(price_type_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный вид цен") from exc
        if pt_id < 1:
            raise HTTPException(status_code=400, detail="Некорректный вид цен")

        data = await file.read()
        if len(data) > _IMPORT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        filename = (file.filename or "").lower()
        if not filename.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Нужен файл Excel в формате .xlsx")

        try:
            meta = await asyncio.to_thread(crm_repo.get_meta)
            price_types = meta.get("price_types") or []
            price_type_name = next(
                (str(pt.get("name") or "").strip() for pt in price_types if int(pt.get("id") or 0) == pt_id),
                "",
            )
            result = await asyncio.to_thread(
                process_yandex_prices_workbook,
                data,
                catalog_repo=catalog_repo,
                price_type_id=pt_id,
                price_type_name=price_type_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("repricer calculate failed")
            raise HTTPException(status_code=500, detail=str(exc) or "Ошибка расчёта") from exc

        preview_rows = [
            {
                "sku": row.sku,
                "name": row.name,
                "seller_price": row.seller_price,
                "showcase_price": row.showcase_price,
                "estimated_card_price": row.estimated_card_price,
                "catalog_price": row.catalog_price,
                "recommended_seller_price": row.recommended_seller_price,
                "updated": row.updated,
                "missing_catalog_price": row.missing_catalog_price,
                "note": row.note,
            }
            for row in result.rows
            if row.showcase_price is not None
        ]
        updated_preview = [row for row in preview_rows if row["updated"]]
        missing_preview = [row for row in preview_rows if row["missing_catalog_price"]]
        preview: list[dict] = []
        seen_skus: set[str] = set()
        for bucket in (updated_preview, missing_preview):
            for row in bucket:
                sku = str(row.get("sku") or "")
                if not sku or sku in seen_skus:
                    continue
                seen_skus.add(sku)
                preview.append(row)
                if len(preview) >= _PREVIEW_MAX_ROWS:
                    break
            if len(preview) >= _PREVIEW_MAX_ROWS:
                break

        return Response(
            content=result.workbook_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="yandex_repricer_result.xlsx"',
                "X-Repricer-Stats": _header_json(result.stats),
                "X-Repricer-Preview": _header_json(preview),
            },
        )
