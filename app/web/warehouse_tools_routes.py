"""HTTP API дополнительных инструментов панели /warehouse."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.cargo_place_calculator import build_cargo_place_template, calculate_cargo_places
from app.excel_to_pdf import excel_to_pdf, excel_to_pdf_download_name, list_excel_to_pdf_profiles
from app.pdf_merge import merge_pdfs_in_order
from app.warehouse_users_repository import WarehouseUserRow
from app.yandex_label_sorter import sort_yandex_labels_from_sheet, warnings_header_json

_PDF_MAX_BYTES = 30 * 1024 * 1024
logger = logging.getLogger(__name__)


def _header_json(value: object) -> str:
    """JSON для HTTP-заголовка: только ASCII (latin-1), иначе Starlette отдаёт 500."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def register_warehouse_tools_routes(
    app,
    catalog_repo,
    require_warehouse_user,
    *,
    google_service_account_file: str = "",
) -> None:
    @app.post("/api/warehouse/tools/pdf-merge")
    async def api_tools_pdf_merge(
        files: list[UploadFile] = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        if not files:
            raise HTTPException(status_code=400, detail="Загрузите хотя бы один PDF")
        parts: list[bytes] = []
        for upload in files:
            data = await upload.read()
            if not data:
                name = (upload.filename or "файл").strip() or "файл"
                raise HTTPException(status_code=400, detail=f"Файл «{name}» пустой")
            parts.append(data)
        try:
            merged = await asyncio.to_thread(merge_pdfs_in_order, parts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=merged,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="merged.pdf"'},
        )

    @app.get("/api/warehouse/tools/excel-to-pdf/profiles")
    async def api_tools_excel_to_pdf_profiles(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"profiles": list_excel_to_pdf_profiles()}

    @app.post("/api/warehouse/tools/excel-to-pdf")
    async def api_tools_excel_to_pdf(
        file: UploadFile = File(...),
        profile: str = Form("free"),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        data = await file.read()
        filename = (file.filename or "table.xlsx").strip() or "table.xlsx"
        try:
            pdf = await asyncio.to_thread(
                excel_to_pdf,
                data,
                profile=profile,
                original_filename=filename,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        out_name = excel_to_pdf_download_name(filename, profile)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )

    @app.get("/api/warehouse/tools/cargo-places/types")
    async def api_cargo_place_types(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"items": await asyncio.to_thread(catalog_repo.list_cargo_place_types)}

    @app.put("/api/warehouse/tools/cargo-places/types")
    async def api_save_cargo_place_types(
        payload: dict = Body(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = payload.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="Передайте список грузомест")
        try:
            saved = await asyncio.to_thread(catalog_repo.save_cargo_place_types, items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": saved}

    @app.get("/api/warehouse/tools/cargo-places/template")
    async def api_cargo_place_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        content = await asyncio.to_thread(build_cargo_place_template)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="cargo-places-template.xlsx"'
            },
        )

    @app.post("/api/warehouse/tools/cargo-places/calculate")
    async def api_calculate_cargo_places(
        file: UploadFile = File(...),
        cargo_place_type_id: int = Form(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Файл пустой")
        try:
            return await asyncio.to_thread(
                calculate_cargo_places,
                data,
                catalog_repo=catalog_repo,
                cargo_place_type_id=cargo_place_type_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/warehouse/tools/cargo-places/products/{product_id}/metrics")
    async def api_update_cargo_product_metrics(
        product_id: int,
        payload: dict = Body(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            item = await asyncio.to_thread(
                catalog_repo.update_product_metrics,
                product_id,
                length_mm=payload.get("length_mm"),
                width_mm=payload.get("width_mm"),
                height_mm=payload.get("height_mm"),
                weight_kg=payload.get("weight_kg"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": item}

    @app.post("/api/warehouse/tools/yandex-label-sort")
    async def api_tools_yandex_label_sort(
        spreadsheet_url: str = Form(...),
        file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        url = str(spreadsheet_url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="Укажите ссылку на Google Таблицу")
        creds = str(google_service_account_file or "").strip()
        if not creds:
            raise HTTPException(
                status_code=500,
                detail="Не настроен GOOGLE_SERVICE_ACCOUNT_FILE",
            )

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="PDF пустой")
        if len(data) > _PDF_MAX_BYTES:
            raise HTTPException(status_code=400, detail="PDF слишком большой (макс. 30 МБ)")
        filename = (file.filename or "").lower()
        if not (filename.endswith(".pdf") or data.startswith(b"%PDF")):
            raise HTTPException(status_code=400, detail="Нужен файл PDF с ярлыками")

        try:
            result = await asyncio.to_thread(
                sort_yandex_labels_from_sheet,
                data,
                url,
                credentials_path=creds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("yandex label sort failed")
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось отсортировать ярлыки: {exc}",
            ) from exc

        return Response(
            content=result.pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="yandex_labels_sorted.pdf"',
                "X-Label-Sort-Stats": _header_json(result.stats),
                "X-Label-Sort-Warnings": warnings_header_json(result.warnings),
            },
        )
