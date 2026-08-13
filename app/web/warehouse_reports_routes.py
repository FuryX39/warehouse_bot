"""HTTP API отчётов панели /warehouse."""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.repositories import InventoryRepository
from app.sales_analysis import MARKETPLACES, MARKETPLACE_IDS, build_sales_analysis
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_acquiring_report import AcquiringExportJobs, parse_year_month
from app.web.warehouse_catalog_routes import _attachment_disposition


def _header_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def register_warehouse_reports_routes(
    app,
    inventory_repo: InventoryRepository,
    catalog_repo: CatalogRepository,
    crm_repo: CrmRepository,
    require_warehouse_user,
    wb_api_token: str = "",
) -> None:
    acquiring_jobs = AcquiringExportJobs(wb_api_token)
    @app.get("/api/warehouse/reports/sales-analysis/meta")
    async def api_sales_analysis_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        meta = await asyncio.to_thread(crm_repo.get_meta)
        return {
            "marketplaces": list(MARKETPLACES),
            "price_types": meta.get("price_types") or [],
        }

    @app.get("/api/warehouse/reports/sales-analysis/export")
    async def api_sales_analysis_export(
        marketplace: str = Query(..., description="ozon | yandex_market | wildberries"),
        price_type_id: int = Query(..., ge=1),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        source = str(marketplace or "").strip().lower()
        if source not in MARKETPLACE_IDS:
            raise HTTPException(
                status_code=400,
                detail="Маркетплейс должен быть одним из: "
                + ", ".join(item["title"] for item in MARKETPLACES),
            )
        try:
            pt_id = int(price_type_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный вид цен") from exc

        def _run() -> tuple[bytes, dict, str]:
            price_types = crm_repo.get_meta().get("price_types") or []
            price_type = next(
                (pt for pt in price_types if int(pt.get("id") or 0) == pt_id),
                None,
            )
            if price_type is None:
                raise ValueError("Вид цен не найден")
            name = str(price_type.get("name") or "").strip() or f"price_type_{pt_id}"
            result = build_sales_analysis(
                inventory_repo,
                catalog_repo,
                marketplace_id=source,
                price_type_id=pt_id,
                price_type_name=name,
            )
            stats = {
                "marketplace": result.marketplace_title,
                "price_type": result.price_type_name,
                "rows": len(result.rows),
                "quantity": result.total_quantity,
                "sum": str(result.total_amount),
                "missing_price_count": result.missing_price_count,
            }
            filename = f"sales_analysis_{result.marketplace_id}_{name[:40]}.xlsx"
            return result.workbook_bytes, stats, filename

        try:
            content, stats, filename = await asyncio.to_thread(_run)
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
                "Content-Disposition": _attachment_disposition(filename),
                "X-Sales-Analysis-Stats": _header_json(stats),
            },
        )

    @app.get("/api/warehouse/reports/wb-acquiring/meta")
    async def api_wb_acquiring_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {"configured": acquiring_jobs.configured()}

    @app.post("/api/warehouse/reports/wb-acquiring/generate")
    async def api_wb_acquiring_generate(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            year, month = parse_year_month(str(body.get("month") or ""))
            job_id = acquiring_jobs.start(year, month)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "running"}

    @app.get("/api/warehouse/reports/wb-acquiring/jobs/{job_id}")
    async def api_wb_acquiring_job(
        job_id: str,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            return acquiring_jobs.snapshot(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Задание не найдено") from exc

    @app.get("/api/warehouse/reports/wb-acquiring/jobs/{job_id}/download")
    async def api_wb_acquiring_download(
        job_id: str,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        try:
            content, filename = acquiring_jobs.download(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Задание не найдено") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": _attachment_disposition(filename)},
        )
