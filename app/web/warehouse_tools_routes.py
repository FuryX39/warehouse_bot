"""HTTP API дополнительных инструментов панели /warehouse."""

from __future__ import annotations

import asyncio

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.pdf_merge import merge_pdfs_in_order
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_tools_routes(app, require_warehouse_user) -> None:
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
