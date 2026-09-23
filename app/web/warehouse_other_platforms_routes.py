"""Прочие маркетплейсы: веб-задания и API упаковщика."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.marketplace_route_sheets import (
    list_route_purchase_statuses,
    route_sheet_content_disposition,
    route_sheet_download_filename,
)
from app.other_marketplace_repository import PLATFORM_VSEINSTRUMENTI, OtherMarketplaceRepository
from app.other_marketplace_service import (
    attach_catalog_images,
    build_other_marketplace_marking_xlsx,
    build_vseinstrumenti_route_pdf,
    create_vseinstrumenti_job,
    pick_other_marketplace_line,
    require_purchase_status,
)
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.web.warehouse_tasks_api_auth import TasksApiActor

logger = logging.getLogger(__name__)


def _http_value_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _xlsx_disposition(filename: str) -> str:
    raw = (filename or "marking.xlsx").strip() or "marking.xlsx"
    ascii_name = "".join(
        ch if ord(ch) < 128 and (ch.isalnum() or ch in "._-") else "_" for ch in raw
    ).strip("._") or "marking.xlsx"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(raw)}'


def register_warehouse_other_platform_routes(
    app,
    repo: OtherMarketplaceRepository,
    catalog_repo: CatalogRepository,
    users_repo: WarehouseUsersRepository,
    require_access,
    require_tasks_access,
    *,
    include_manager: bool = True,
    packer_prefixes: tuple[str, ...] | None = None,
) -> None:
    def _names() -> dict[int, str]:
        return {int(item["id"]): str(item["display_name"]) for item in users_repo.list_assignee_picker()}

    def _job_dict(job, *, include_lines: bool) -> dict:
        return repo.job_to_dict(job, include_lines=include_lines, packer_names=_names())

    if include_manager:

        @app.get("/api/warehouse/other-platforms/meta")
        async def api_other_platforms_meta(
            _: WarehouseUserRow | None = Depends(require_access),
        ) -> dict:
            return {
                "platforms": [
                    {"id": PLATFORM_VSEINSTRUMENTI, "title": "ВсеИнструменты", "enabled": True},
                ],
                "assignees": users_repo.list_assignee_picker(),
                "purchase_statuses": list_route_purchase_statuses(),
            }

        @app.get("/api/warehouse/other-platforms/vseinstrumenti/jobs")
        async def api_vi_jobs(
            _: WarehouseUserRow | None = Depends(require_access),
        ) -> dict:
            jobs = await asyncio.to_thread(repo.list_jobs, PLATFORM_VSEINSTRUMENTI)
            return {"jobs": [_job_dict(job, include_lines=False) for job in jobs]}

        @app.post("/api/warehouse/other-platforms/vseinstrumenti/jobs")
        async def api_vi_create_job(
            file: UploadFile = File(...),
            transfer_number: str = Form(""),
            purchase_status: str = Form(""),
            packer_user_ids: str = Form(""),
            user: WarehouseUserRow | None = Depends(require_access),
        ) -> dict:
            content = await file.read()
            ids = [part.strip() for part in str(packer_user_ids or "").replace(";", ",").split(",") if part.strip()]
            try:
                job, warnings = await asyncio.to_thread(
                    create_vseinstrumenti_job,
                    catalog=catalog_repo,
                    repo=repo,
                    content=content,
                    filename=file.filename or "",
                    transfer_number=transfer_number,
                    purchase_status=purchase_status,
                    packer_user_ids=ids,
                    created_by_user_id=int(user.id) if user is not None else None,
                )
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": _job_dict(job, include_lines=True), "warnings": warnings}

        @app.post("/api/warehouse/other-platforms/jobs/{job_id}/cancel")
        async def api_vi_cancel(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_access),
        ) -> dict:
            try:
                job = await asyncio.to_thread(repo.cancel_job, job_id)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": _job_dict(job, include_lines=False)}

        @app.post("/api/warehouse/other-platforms/jobs/{job_id}/purchase-status")
        async def api_vi_purchase_status(
            job_id: int,
            body: dict,
            _: WarehouseUserRow | None = Depends(require_access),
        ) -> dict:
            try:
                status_name = require_purchase_status(str((body or {}).get("purchase_status") or ""))
                job = await asyncio.to_thread(repo.set_purchase_status, job_id, status_name)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": _job_dict(job, include_lines=False)}

        @app.get("/api/warehouse/other-platforms/jobs/{job_id}/marking.xlsx")
        async def api_vi_marking(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_access),
        ) -> Response:
            job = repo.get_job(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            content = await asyncio.to_thread(build_other_marketplace_marking_xlsx, job)
            filename = f"ВсеИнструменты {job.order_number} КИЗ.xlsx"
            return Response(
                content=content,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": _xlsx_disposition(filename)},
            )

    if packer_prefixes is None:
        packer_prefixes = ()
    for prefix in packer_prefixes:
        _register_packer(app, prefix, repo, catalog_repo, require_tasks_access, _job_dict)


def _register_packer(app, prefix: str, repo, catalog_repo, auth_dep, job_dict) -> None:
    tag = prefix.strip("/").replace("/", "_")

    def _actor(actor: TasksApiActor = Depends(auth_dep)) -> TasksApiActor:
        return actor

    def _require(actor: TasksApiActor, job_id: int) -> int:
        if actor.user is None:
            raise HTTPException(status_code=400, detail="Нужен вход пользователем склада")
        job = repo.get_job(job_id, include_lines=False)
        if job is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if int(actor.user.id) not in job.packer_user_ids and not actor.user.is_admin:
            raise HTTPException(status_code=403, detail="Задание назначено другому упаковщику")
        return int(actor.user.id)

    @app.get(f"{prefix}/my", name=f"other_mp_my_{tag}")
    async def api_other_mp_my(actor: TasksApiActor = Depends(_actor)) -> dict:
        if actor.user is None:
            raise HTTPException(status_code=400, detail="Нужен вход пользователем склада")
        jobs = await asyncio.to_thread(repo.list_my_jobs, int(actor.user.id))
        return {"jobs": [job_dict(job, include_lines=False) for job in jobs]}

    @app.get(f"{prefix}/jobs/{{job_id}}/pack", name=f"other_mp_pack_{tag}")
    async def api_other_mp_pack(job_id: int, actor: TasksApiActor = Depends(_actor)) -> dict:
        _require(actor, job_id)
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        payload = job_dict(job, include_lines=True)
        attach_catalog_images(catalog_repo, payload)
        return {"job": payload}

    @app.post(f"{prefix}/jobs/{{job_id}}/scan", name=f"other_mp_scan_{tag}")
    async def api_other_mp_scan(job_id: int, body: dict, actor: TasksApiActor = Depends(_actor)) -> dict:
        _require(actor, job_id)
        barcode = str((body or {}).get("barcode") or (body or {}).get("code") or "")
        try:
            result = await asyncio.to_thread(
                pick_other_marketplace_line,
                catalog=catalog_repo,
                repo=repo,
                job_id=job_id,
                raw=barcode,
            )
        except ValueError as exc:
            shown = barcode if len(barcode) <= 32 else f"{barcode[:16]}…({len(barcode)})"
            logger.warning("VI scan rejected job=%s: %s [%s]", job_id, exc, shown)
            raise _http_value_error(exc) from exc
        job_payload = result.get("job")
        if isinstance(job_payload, dict):
            attach_catalog_images(catalog_repo, job_payload)
        return result

    @app.post(f"{prefix}/jobs/{{job_id}}/pick", name=f"other_mp_pick_{tag}")
    async def api_other_mp_pick(job_id: int, body: dict, actor: TasksApiActor = Depends(_actor)) -> dict:
        _require(actor, job_id)
        sku = str((body or {}).get("sku") or "")
        product_id = (body or {}).get("product_id")
        try:
            product_id = int(product_id) if product_id not in (None, "") else None
        except (TypeError, ValueError):
            product_id = None
        try:
            result = await asyncio.to_thread(
                pick_other_marketplace_line,
                catalog=catalog_repo,
                repo=repo,
                job_id=job_id,
                raw="",
                sku=sku,
                product_id=product_id,
            )
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        job_payload = result.get("job")
        if isinstance(job_payload, dict):
            attach_catalog_images(catalog_repo, job_payload)
        return result

    @app.post(f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/set-status", name=f"other_mp_line_status_{tag}")
    async def api_other_mp_line_status(
        job_id: int,
        line_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        _require(actor, job_id)
        status = str((body or {}).get("status") or "")
        try:
            job = await asyncio.to_thread(repo.set_line_status, job_id, line_id, status)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        payload = job_dict(job, include_lines=True)
        attach_catalog_images(catalog_repo, payload)
        return {"job": payload}

    @app.post(f"{prefix}/jobs/{{job_id}}/route-sheet.pdf", name=f"other_mp_a4_{tag}")
    async def api_other_mp_a4(job_id: int, body: dict, actor: TasksApiActor = Depends(_actor)) -> Response:
        _require(actor, job_id)
        job = repo.get_job(job_id, include_lines=False)
        if job is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        try:
            pdf = build_vseinstrumenti_route_pdf(
                job,
                cargo_type=str((body or {}).get("cargo_type") or "pallets"),
                cargo_count=int((body or {}).get("cargo_count") or 1),
            )
        except (TypeError, ValueError) as exc:
            raise _http_value_error(exc) from exc
        filename = route_sheet_download_filename(PLATFORM_VSEINSTRUMENTI, job.order_number)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": route_sheet_content_disposition(filename)},
        )
