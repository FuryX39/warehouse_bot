"""HTTP API FBO WB new: таблицы кабинета, без записи в WB."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.wb_fbo_sheet_repository import BOX_ASSIGNED, WbFboSheetRepository
from app.wb_fbo_sheet_service import (
    attach_sheet_images,
    create_wb_fbo_sheet_job,
    find_box_by_scan,
    pdf_for_boxes,
    qty_warning_for_box,
    resolve_sheet_scan,
)
from app.wb_fbo_sheet_xlsx import fill_boxes_xlsx
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.web.warehouse_wb_fbo_routes import (
    _attachment_disposition,
    _http_value_error,
    _parse_packer_ids,
)


def register_warehouse_wb_fbo_sheet_routes(
    app,
    packing_repo: WbFboSheetRepository,
    catalog_repo: CatalogRepository,
    users_repo: WarehouseUsersRepository,
    require_warehouse_user,
    require_tasks_access,
    *,
    include_manager: bool = True,
    packer_prefixes: tuple[str, ...] | None = None,
) -> None:
    def _names() -> dict[int, str]:
        return {
            int(item["id"]): str(item.get("display_name") or item.get("login") or item["id"])
            for item in users_repo.list_assignee_picker()
        }

    def _packer_job_payload(job_id: int) -> dict[str, Any]:
        job = packing_repo.get_job(job_id, include_lines=True)
        if job is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        payload = packing_repo.job_to_dict(job, include_lines=True)
        attach_sheet_images(catalog_repo, payload)
        return payload

    if include_manager:

        @app.get("/api/warehouse/marketplaces/wb-fbo-new/meta")
        async def api_wb_fbo_sheet_meta(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            return {"assignees": users_repo.list_assignee_picker()}

        @app.get("/api/warehouse/marketplaces/wb-fbo-new/jobs")
        async def api_wb_fbo_sheet_jobs(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            jobs = packing_repo.list_jobs(packer_names=_names())
            return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

        @app.post("/api/warehouse/marketplaces/wb-fbo-new/jobs")
        async def api_wb_fbo_sheet_jobs_create(
            goods: UploadFile | None = File(None),
            boxes: UploadFile | None = File(None),
            packer_user_ids: str = Form("[]"),
            supply_id: str = Form(""),
            warehouse_name: str = Form(""),
            seller_name: str = Form(""),
            plan_date: str = Form(""),
            box_type: str = Form("Короб"),
            user: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            packers = _parse_packer_ids(packer_user_ids)
            if not packers:
                raise HTTPException(status_code=400, detail="Назначьте хотя бы одного упаковщика")
            if goods is None or boxes is None:
                raise HTTPException(
                    status_code=400,
                    detail="Прикрепите таблицу товаров и таблицу ШК коробов",
                )
            goods_bytes = await goods.read()
            boxes_bytes = await boxes.read()
            try:
                job = await asyncio.to_thread(
                    create_wb_fbo_sheet_job,
                    catalog=catalog_repo,
                    packing_repo=packing_repo,
                    goods_xlsx=goods_bytes,
                    boxes_xlsx=boxes_bytes,
                    packer_user_ids=packers,
                    created_by_user_id=int(user.id) if user else None,
                    supply_id=supply_id,
                    warehouse_name=warehouse_name,
                    seller_name=seller_name,
                    plan_date=plan_date,
                    box_type=box_type,
                )
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.get("/api/warehouse/marketplaces/wb-fbo-new/jobs/{job_id}")
        async def api_wb_fbo_sheet_job_get(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.post("/api/warehouse/marketplaces/wb-fbo-new/jobs/{job_id}/cancel")
        async def api_wb_fbo_sheet_job_cancel(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            job = packing_repo.cancel_job(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job)}

        @app.get("/api/warehouse/marketplaces/wb-fbo-new/jobs/{job_id}/boxes.xlsx")
        async def api_wb_fbo_sheet_boxes_xlsx(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> Response:
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            try:
                original = packing_repo.read_stored(job.boxes_stored_name)
                filled = fill_boxes_xlsx(
                    original,
                    [
                        packing_repo.box_to_dict(box)
                        | {
                            "box_id": box.box_human_id,
                            "product_barcode": box.product_barcode,
                            "item_qty": box.item_qty,
                        }
                        for box in job.boxes
                        if box.status == BOX_ASSIGNED
                    ],
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return Response(
                content=filled,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": _attachment_disposition(
                        f"wb_fbo_sheet_{job_id}_boxes.xlsx"
                    )
                },
            )

    def _require_packer(actor: TasksApiActor, job_id: int) -> int:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        if not packing_repo.user_can_pack(job_id, int(actor.user.id)):
            raise HTTPException(status_code=403, detail="Задание назначено другому сотруднику")
        return int(actor.user.id)

    if packer_prefixes is None:
        packer_prefixes = ()
    for prefix in packer_prefixes:
        _register_sheet_packer_prefix(
            app,
            prefix,
            packing_repo,
            catalog_repo,
            require_tasks_access if prefix.startswith("/api/v1/") else require_warehouse_user,
            require_packer=_require_packer,
            packer_job_payload=_packer_job_payload,
        )


def _register_sheet_packer_prefix(
    app,
    prefix: str,
    packing_repo: WbFboSheetRepository,
    catalog_repo: CatalogRepository,
    auth_dep,
    *,
    require_packer,
    packer_job_payload,
) -> None:
    @app.get(f"{prefix}/my")
    async def api_sheet_my(actor: TasksApiActor = Depends(auth_dep)) -> dict:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        jobs = await asyncio.to_thread(packing_repo.list_my_jobs, int(actor.user.id))
        return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

    @app.get(f"{prefix}/jobs/{{job_id}}/pack")
    async def api_sheet_pack(
        job_id: int,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> dict:
        require_packer(actor, job_id)
        try:
            job = await asyncio.to_thread(packer_job_payload, job_id)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"job": job}

    @app.post(f"{prefix}/jobs/{{job_id}}/print-boxes")
    async def api_sheet_print_boxes(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> dict:
        require_packer(actor, job_id)
        payload = body if isinstance(body, dict) else {}
        try:
            count = int(payload.get("count") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректное количество") from exc

        def _run():
            boxes = packing_repo.next_unprinted_boxes(job_id, count)
            printed = packing_repo.mark_boxes_printed(job_id, [box.id for box in boxes])
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise ValueError("Задание не найдено")
            pdf = pdf_for_boxes(job, printed)
            pages = _split_pdf_pages(pdf, len(printed))
            return printed, pages, packer_job_payload(job_id)

        try:
            printed, pages, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        pdfs_b64 = [base64.b64encode(page).decode("ascii") for page in pages]
        return {
            "boxes": [packing_repo.box_to_dict(box) for box in printed],
            "pdfs_base64": pdfs_b64,
            "pdf_base64": pdfs_b64[0] if pdfs_b64 else "",
            "job": job,
        }

    @app.post(f"{prefix}/jobs/{{job_id}}/reprint-box")
    async def api_sheet_reprint_box(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> dict:
        require_packer(actor, job_id)
        payload = body if isinstance(body, dict) else {}
        try:
            box_id = int(payload.get("box_id") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный короб") from exc

        def _run():
            box = packing_repo.get_box(job_id, box_id)
            if box is None:
                raise ValueError("Короб не найден")
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise ValueError("Задание не найдено")
            pdf = pdf_for_boxes(job, [box])
            return box, pdf, packer_job_payload(job_id)

        try:
            box, pdf, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        b64 = base64.b64encode(pdf).decode("ascii")
        return {
            "box": packing_repo.box_to_dict(box),
            "pdf_base64": b64,
            "pdfs_base64": [b64],
            "job": job,
        }

    @app.post(f"{prefix}/jobs/{{job_id}}/resolve")
    async def api_sheet_resolve(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> dict:
        require_packer(actor, job_id)
        barcode = str((body or {}).get("barcode") or (body or {}).get("code") or "")
        try:
            resolved = await asyncio.to_thread(
                resolve_sheet_scan, catalog_repo, packing_repo, job_id, barcode
            )
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return resolved

    @app.post(f"{prefix}/jobs/{{job_id}}/assign")
    async def api_sheet_assign(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        payload = body if isinstance(body, dict) else {}
        barcode = str(payload.get("barcode") or payload.get("box_code") or "")
        product_barcode = str(payload.get("product_barcode") or "")
        try:
            qty = int(payload.get("quantity") or payload.get("qty") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Некорректное количество") from exc

        def _run():
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise ValueError("Задание не найдено")
            found = find_box_by_scan(job, barcode)
            if found is None:
                raise ValueError("Сначала выберите товар, затем пикните ШК короба WB")
            box_id = int(found.id)
            assigned = packing_repo.assign_box(
                job_id,
                user_id,
                box_id=box_id,
                product_barcode=product_barcode,
                item_qty=qty,
            )
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise ValueError("Задание не найдено")
            warning = qty_warning_for_box(catalog_repo, job, assigned)
            item = packing_repo.box_to_dict(assigned)
            item["qty_warning"] = warning
            return item, warning, packer_job_payload(job_id)

        try:
            item, warning, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {
            "box": item,
            "qty_warning": warning,
            "qty_warnings": [warning] if warning else [],
            "job": job,
        }

    @app.get(f"{prefix}/jobs/{{job_id}}/boxes/{{box_id}}/label")
    async def api_sheet_box_label(
        job_id: int,
        box_id: int,
        actor: TasksApiActor = Depends(auth_dep),
    ) -> Response:
        require_packer(actor, job_id)

        def _run() -> bytes:
            box = packing_repo.get_box(job_id, box_id)
            if box is None:
                raise ValueError("Короб не найден")
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise ValueError("Задание не найдено")
            return pdf_for_boxes(job, [box])

        try:
            pdf = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": _attachment_disposition(f"wb_fbo_sheet_box_{box_id}.pdf")
            },
        )


def _split_pdf_pages(pdf: bytes, expected: int) -> list[bytes]:
    from app.fbs_labels_common import split_pdf_into_pages

    pages = split_pdf_into_pages(pdf)
    if len(pages) == expected:
        return pages
    return [pdf] if expected == 1 else pages
