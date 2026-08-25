"""HTTP API кодов маркировки для панели /warehouse."""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.marking.export import build_marking_codes_export
from app.marking.match import match_datamatrix_codes, match_result_preview
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_catalog_routes import _attachment_disposition


def _request_text(body: dict | None) -> str:
    payload = body if isinstance(body, dict) else {}
    if isinstance(payload.get("codes"), list):
        return "\n".join(str(item or "") for item in payload["codes"])
    return str(payload.get("text") or "")


def register_warehouse_marking_routes(
    app,
    catalog_repo: CatalogRepository,
    require_warehouse_user,
) -> None:
    @app.post("/api/warehouse/marking/codes/parse")
    async def api_marking_codes_parse(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        text = _request_text(body)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Вставьте коды Data Matrix")

        def _run() -> dict:
            result = match_datamatrix_codes(text, catalog_repo)
            return match_result_preview(result)

        try:
            return await asyncio.to_thread(_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/warehouse/marking/codes/export")
    async def api_marking_codes_export(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        text = _request_text(body)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Вставьте коды Data Matrix")

        def _run() -> tuple[bytes, dict]:
            result = match_datamatrix_codes(text, catalog_repo)
            preview = match_result_preview(result)
            return build_marking_codes_export(result), preview["stats"]

        try:
            content, stats = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc

        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": _attachment_disposition("marking_codes.xlsx"),
                "X-Marking-Codes-Stats": json.dumps(
                    stats, ensure_ascii=True, separators=(",", ":")
                ),
            },
        )
