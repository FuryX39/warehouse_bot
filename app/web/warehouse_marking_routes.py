"""HTTP API кодов маркировки для панели /warehouse."""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.marking.export import build_marking_codes_export
from app.marking.gtin_import import build_gtin_import_template, import_gtins_from_xlsx
from app.marking.match import match_datamatrix_codes, match_result_preview
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_catalog_routes import _attachment_disposition

_IMPORT_MAX_BYTES = 10 * 1024 * 1024


def _request_codes(body: dict | None) -> list[str]:
    payload = body if isinstance(body, dict) else {}
    if isinstance(payload.get("codes"), list):
        return [str(item or "") for item in payload["codes"]]
    text = str(payload.get("text") or "")
    return text.split("\n") if text else []


def _codes_to_text(codes: list[str]) -> str:
    return "\n".join(item for item in codes if str(item).strip())


def register_warehouse_marking_routes(
    app,
    catalog_repo: CatalogRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/marking/gtins")
    async def api_marking_gtins_list(
        q: str = Query(""),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = await asyncio.to_thread(catalog_repo.list_product_gtin_rows, q.strip())
        return {"products": rows}

    @app.post("/api/warehouse/marking/gtins")
    async def api_marking_gtins_add(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            product_id = int(body.get("product_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный товар") from exc
        gtin = str(body.get("gtin") or "")
        try:
            action = await asyncio.to_thread(catalog_repo.add_product_gtin, product_id, gtin)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "action": action}

    @app.post("/api/warehouse/marking/gtins/remove")
    async def api_marking_gtins_remove(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            product_id = int(body.get("product_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный товар") from exc
        gtin = str(body.get("gtin") or "")
        try:
            removed = await asyncio.to_thread(
                catalog_repo.remove_product_gtin, product_id, gtin
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="GTIN не найден у этого товара")
        return {"ok": True}

    @app.get("/api/warehouse/marking/gtins/template")
    async def api_marking_gtins_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            content = await asyncio.to_thread(build_gtin_import_template, catalog_repo)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": _attachment_disposition("marking_gtin_template.xlsx"),
            },
        )

    @app.post("/api/warehouse/marking/gtins/import", response_model=None)
    async def api_marking_gtins_import(
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ):
        data = await file.read()
        if len(data) > _IMPORT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        filename = (file.filename or "").lower()
        if not filename.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Нужен файл Excel в формате .xlsx")
        try:
            result = await asyncio.to_thread(import_gtins_from_xlsx, catalog_repo, data)
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        stats = {
            "created": result.created,
            "skipped": result.skipped,
            "failed": result.failed,
            "total_rows": result.total_rows,
        }
        if result.error_report:
            return Response(
                content=result.error_report,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": _attachment_disposition(
                        "marking_gtin_import_errors.xlsx"
                    ),
                    "X-Import-Created": str(result.created),
                    "X-Import-Skipped": str(result.skipped),
                    "X-Import-Failed": str(result.failed),
                    "X-Import-Total": str(result.total_rows),
                    "X-Marking-Gtin-Import": json.dumps(
                        stats, ensure_ascii=True, separators=(",", ":")
                    ),
                },
            )
        return {"ok": True, **stats}

    @app.post("/api/warehouse/marking/codes/parse")
    async def api_marking_codes_parse(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        text = _codes_to_text(_request_codes(body))
        if not text.strip():
            raise HTTPException(status_code=400, detail="Нет кодов Data Matrix")

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
        text = _codes_to_text(_request_codes(body))
        if not text.strip():
            raise HTTPException(status_code=400, detail="Нет кодов Data Matrix")

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
