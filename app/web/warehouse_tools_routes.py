"""HTTP API дополнительных инструментов панели /warehouse."""

from __future__ import annotations

import asyncio

from fastapi import Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.cargo_place_calculator import build_cargo_place_template, calculate_cargo_places
from app.pdf_merge import merge_pdfs_in_order
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_tools_routes(app, catalog_repo, require_warehouse_user) -> None:
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
