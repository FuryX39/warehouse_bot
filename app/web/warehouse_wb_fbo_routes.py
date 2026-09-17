"""HTTP API заданий FBO WB: панель /warehouse и десктоп /api/v1."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from urllib.parse import quote

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.fbs_labels_common import build_labels_zip
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.wb_fbo_packing_repository import WbFboPackingRepository
from app.wb_fbo_packing_service import (
    attach_fbo_box_qty_warnings,
    create_wb_fbo_packing_job,
    lookup_fbo_pick,
    preview_wb_fbo_supply,
    resolve_fbo_scan,
)
from app.wb_fbs_labels import get_configured_wb_adapter
from app.web.warehouse_tasks_api_auth import TasksApiActor


def _attachment_disposition(filename: str) -> str:
    raw = (filename or "file.pdf").strip() or "file.pdf"
    ascii_name = "".join(
        ch if ord(ch) < 128 and (ch.isalnum() or ch in "._-") else "_" for ch in raw
    )
    ascii_name = ascii_name.strip("._") or "file.pdf"
    utf8_name = quote(raw)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


def _http_value_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _parse_packer_ids(raw: object) -> list[int]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text.replace(",", " ").split()
        items = parsed if isinstance(parsed, list) else [parsed]
    else:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in items:
        try:
            uid = int(item)
        except (TypeError, ValueError):
            continue
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _attach_catalog_images(catalog_repo: CatalogRepository, payload: dict[str, Any]) -> None:
    buckets: list[dict[str, Any]] = []
    remaining = payload.get("remaining_groups")
    if isinstance(remaining, list):
        buckets.extend(item for item in remaining if isinstance(item, dict))
    for key in ("lines", "active_lines"):
        rows = payload.get(key)
        if isinstance(rows, list):
            buckets.extend(item for item in rows if isinstance(item, dict))
    active = payload.get("active_line")
    if isinstance(active, dict):
        buckets.append(active)
    pids = [int(item["product_id"]) for item in buckets if item.get("product_id")]
    urls = catalog_repo.image_urls_by_product_ids(pids) if pids else {}
    for item in buckets:
        pid = item.get("product_id")
        if pid:
            item["image_url"] = urls.get(int(pid), "") or item.get("image_url") or ""


def _flag(body: dict | None, key: str, default: bool = False) -> bool:
    if not isinstance(body, dict) or key not in body:
        return default
    raw = body.get(key)
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if default:
        return text not in {"0", "false", "no", "off"}
    return text in {"1", "true", "yes", "on"}


def register_warehouse_wb_fbo_routes(
    app,
    packing_repo: WbFboPackingRepository,
    catalog_repo: CatalogRepository,
    users_repo: WarehouseUsersRepository,
    coordinator,
    require_warehouse_user,
    require_tasks_access,
    *,
    include_manager: bool = True,
    packer_prefixes: tuple[str, ...] | None = None,
) -> None:
    def _wb():
        adapter = get_configured_wb_adapter(coordinator) if coordinator is not None else None
        if adapter is None or not adapter.is_configured():
            raise HTTPException(status_code=400, detail="Wildberries не настроен")
        return adapter

    def _names() -> dict[int, str]:
        return {
            int(item["id"]): str(item.get("display_name") or item.get("login") or item["id"])
            for item in users_repo.list_assignee_picker()
        }

    if include_manager:

        @app.get("/api/warehouse/marketplaces/wb-fbo/meta")
        async def api_wb_fbo_meta(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            return {"assignees": users_repo.list_assignee_picker()}

        @app.get("/api/warehouse/marketplaces/wb-fbo/preview")
        async def api_wb_fbo_preview(
            supply_id: str = "",
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                return await asyncio.to_thread(preview_wb_fbo_supply, _wb(), supply_id)
            except ValueError as exc:
                raise _http_value_error(exc) from exc

        @app.get("/api/warehouse/marketplaces/wb-fbo/jobs")
        async def api_wb_fbo_jobs(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            jobs = packing_repo.list_jobs(packer_names=_names())
            return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

        @app.post("/api/warehouse/marketplaces/wb-fbo/jobs")
        async def api_wb_fbo_jobs_create(
            supply_id: str = Form(""),
            pallet_count: str = Form("1"),
            city: str = Form(""),
            packer_user_ids: str = Form("[]"),
            qr: UploadFile | None = File(None),
            user: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            packers = _parse_packer_ids(packer_user_ids)
            if not packers:
                raise HTTPException(status_code=400, detail="Назначьте хотя бы одного упаковщика")
            if qr is None:
                raise HTTPException(status_code=400, detail="Прикрепите PDF с QR поставки")
            content = await qr.read()
            try:
                job = await asyncio.to_thread(
                    create_wb_fbo_packing_job,
                    adapter=_wb(),
                    catalog=catalog_repo,
                    packing_repo=packing_repo,
                    supply_id=supply_id,
                    pallet_count=pallet_count,
                    city=city,
                    packer_user_ids=packers,
                    created_by_user_id=int(user.id) if user else None,
                    supply_qr_pdf=content,
                )
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.get("/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}")
        async def api_wb_fbo_job_get(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.post("/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/cancel")
        async def api_wb_fbo_job_cancel(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                job = packing_repo.cancel_job(job_id)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job)}

        def _manager_pdf(job_id: int, kind: str, filename: str) -> Response:
            try:
                pdf = packing_repo.read_job_pdf(job_id, kind)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": _attachment_disposition(filename)},
            )

        @app.get("/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/supply-qr.pdf")
        async def api_wb_fbo_supply_qr(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> Response:
            return _manager_pdf(job_id, "supply-qr", f"wb_fbo_{job_id}_supply_qr.pdf")

        @app.get("/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/pallet-sheets.pdf")
        async def api_wb_fbo_pallet_sheets(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> Response:
            return _manager_pdf(job_id, "pallet-sheets", f"wb_fbo_{job_id}_pallets.pdf")

        @app.get("/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/box-labels.pdf")
        async def api_wb_fbo_box_labels(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> Response:
            return _manager_pdf(job_id, "box-labels", f"wb_fbo_{job_id}_boxes.pdf")

    def _require_packer(actor: TasksApiActor, job_id: int) -> int:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        if not packing_repo.user_can_pack(job_id, int(actor.user.id)):
            raise HTTPException(status_code=403, detail="Задание назначено другому сотруднику")
        return int(actor.user.id)

    def _packer_job_payload(job_id: int) -> dict[str, Any]:
        job = packing_repo.get_job(job_id, include_lines=True)
        if job is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        remaining = packing_repo.remaining_groups_from_lines(job.lines)
        product_ids = [int(item["product_id"]) for item in remaining if item.get("product_id")]
        barcodes = catalog_repo.first_barcode_by_product_ids(product_ids) if product_ids else {}
        for item in remaining:
            pid = item.get("product_id")
            item["barcode"] = barcodes.get(int(pid), "") if pid else ""
        payload = packing_repo.job_to_dict(job, include_lines=True)
        payload["remaining_groups"] = remaining
        attach_fbo_box_qty_warnings(catalog_repo, payload)
        _attach_catalog_images(catalog_repo, payload)
        return payload

    if packer_prefixes is None:
        packer_prefixes = ()
    for prefix in packer_prefixes:
        _register_packer_prefix(
            app,
            prefix,
            packing_repo,
            catalog_repo,
            require_tasks_access if prefix.startswith("/api/v1/") else require_warehouse_user,
            v1=prefix.startswith("/api/v1/"),
            require_packer=_require_packer,
            packer_job_payload=_packer_job_payload,
        )


def _register_packer_prefix(
    app,
    prefix: str,
    packing_repo: WbFboPackingRepository,
    catalog_repo: CatalogRepository,
    auth_dep,
    *,
    v1: bool,
    require_packer,
    packer_job_payload,
) -> None:
    tag = "v1" if v1 else "wh"
    if v1:

        def _actor(actor: TasksApiActor = Depends(auth_dep)) -> TasksApiActor:
            return actor

    else:

        def _actor(user: WarehouseUserRow = Depends(auth_dep)) -> TasksApiActor:
            return TasksApiActor(user=user, via_api_token=False)

    @app.get(f"{prefix}/my", name=f"wb_fbo_packing_my_{tag}")
    async def api_wb_fbo_my(actor: TasksApiActor = Depends(_actor)) -> dict:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        jobs = await asyncio.to_thread(packing_repo.list_my_jobs, int(actor.user.id))
        return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

    @app.get(f"{prefix}/jobs/{{job_id}}/pack", name=f"wb_fbo_packing_pack_{tag}")
    async def api_wb_fbo_pack(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        require_packer(actor, job_id)
        try:
            job = await asyncio.to_thread(packer_job_payload, job_id)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"job": job}

    def _allocate_response(
        job_id: int,
        user_id: int,
        sku: str,
        product_id: int | None,
        *,
        barcode: str = "",
        batch: bool = False,
        include_pdf: bool = True,
        auto_close: bool = False,
    ) -> dict:
        lines = packing_repo.allocate_lines(
            job_id,
            user_id,
            sku=sku,
            product_id=product_id,
            barcode=barcode,
            batch=batch,
            auto_close=auto_close,
        )
        pdfs_b64: list[str] = []
        if include_pdf:
            for line in lines:
                pdf = packing_repo.read_line_pdf(job_id, line.id)
                pdfs_b64.append(base64.b64encode(pdf).decode("ascii"))
        job = packer_job_payload(job_id)
        by_id = {
            int(item["id"]): item
            for item in (job.get("lines") or [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        line_dicts: list[dict[str, Any]] = []
        for line in lines:
            item = packing_repo.line_to_dict(line)
            src = by_id.get(int(item["id"]))
            item["qty_warning"] = str((src or {}).get("qty_warning") or "")
            line_dicts.append(item)
        qty_warnings = [item["qty_warning"] for item in line_dicts if item.get("qty_warning")]
        return {
            "line": line_dicts[0],
            "lines": line_dicts,
            "job": job,
            "pdf_base64": pdfs_b64[0] if pdfs_b64 else "",
            "pdfs_base64": pdfs_b64,
            "qty_warning": "\n".join(qty_warnings),
            "qty_warnings": qty_warnings,
        }

    @app.post(f"{prefix}/jobs/{{job_id}}/scan-product", name=f"wb_fbo_scan_product_{tag}")
    async def api_wb_fbo_scan_product(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        barcode = str((body or {}).get("barcode") or (body or {}).get("code") or "")
        payload = body if isinstance(body, dict) else {}
        try:
            resolved = resolve_fbo_scan(catalog_repo, packing_repo, job_id, barcode)
            return await asyncio.to_thread(
                _allocate_response,
                job_id,
                user_id,
                resolved.sku,
                resolved.product_id,
                barcode=barcode,
                batch=_flag(payload, "batch"),
                include_pdf=_flag(payload, "include_pdf", True),
                auto_close=_flag(payload, "auto_close"),
            )
        except ValueError as exc:
            raise _http_value_error(exc) from exc

    @app.post(f"{prefix}/jobs/{{job_id}}/pick-sku", name=f"wb_fbo_pick_sku_{tag}")
    async def api_wb_fbo_pick_sku(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        payload = body if isinstance(body, dict) else {}
        sku = str(payload.get("sku") or "")
        raw_pid = payload.get("product_id")
        product_id = None
        if raw_pid not in (None, ""):
            try:
                product_id = int(raw_pid)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Некорректный товар") from exc
        try:
            sku, product_id = lookup_fbo_pick(catalog_repo, sku, product_id)
            return await asyncio.to_thread(
                _allocate_response,
                job_id,
                user_id,
                sku,
                product_id,
                batch=_flag(payload, "batch"),
                include_pdf=_flag(payload, "include_pdf", True),
                auto_close=_flag(payload, "auto_close"),
            )
        except ValueError as ext:
            raise _http_value_error(ext) from ext

    @app.get(f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/label", name=f"wb_fbo_line_label_{tag}")
    async def api_wb_fbo_line_label(
        job_id: int,
        line_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> Response:
        require_packer(actor, job_id)
        try:
            pdf = await asyncio.to_thread(packing_repo.read_line_pdf, job_id, line_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment_disposition(f"wb_fbo_line_{line_id}.pdf")},
        )

    @app.get(f"{prefix}/jobs/{{job_id}}/line-labels.zip", name=f"wb_fbo_line_labels_zip_{tag}")
    async def api_wb_fbo_line_labels_zip(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> Response:
        require_packer(actor, job_id)

        def _run() -> bytes:
            files = [
                (f"{line_id}.pdf", pdf)
                for line_id, pdf in packing_repo.list_line_pdfs(job_id)
            ]
            if not files:
                raise ValueError("Нет ярлыков в задании")
            return build_labels_zip(files)

        try:
            content = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": _attachment_disposition(f"wb_fbo_job_{job_id}_labels.zip")},
        )

    @app.post(
        f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/close",
        name=f"wb_fbo_line_close_{tag}",
    )
    async def api_wb_fbo_line_close(
        job_id: int,
        line_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)

        def _run():
            line = packing_repo.close_line(job_id, line_id, user_id)
            return packing_repo.line_to_dict(line), packer_job_payload(job_id)

        try:
            line, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"line": line, "job": job}

    @app.post(
        f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/cancel-print",
        name=f"wb_fbo_line_cancel_{tag}",
    )
    async def api_wb_fbo_line_cancel(
        job_id: int,
        line_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        require_packer(actor, job_id)

        def _run():
            line = packing_repo.cancel_print(job_id, line_id)
            return packing_repo.line_to_dict(line), packer_job_payload(job_id)

        try:
            line, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"line": line, "job": job}

    @app.post(
        f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/set-status",
        name=f"wb_fbo_line_set_status_{tag}",
    )
    async def api_wb_fbo_line_set_status(
        job_id: int,
        line_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        status = str((body or {}).get("status") or "").strip()

        def _run():
            line = packing_repo.set_line_status(job_id, line_id, user_id, status)
            return packing_repo.line_to_dict(line), packer_job_payload(job_id)

        try:
            line, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"line": line, "job": job}

    @app.get(f"{prefix}/jobs/{{job_id}}/supply-qr.pdf", name=f"wb_fbo_packer_qr_{tag}")
    async def api_wb_fbo_packer_qr(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> Response:
        require_packer(actor, job_id)
        try:
            pdf = packing_repo.read_job_pdf(job_id, "supply-qr")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment_disposition("supply_qr.pdf")},
        )

    @app.get(f"{prefix}/jobs/{{job_id}}/pallet-sheets.pdf", name=f"wb_fbo_packer_sheets_{tag}")
    async def api_wb_fbo_packer_sheets(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> Response:
        require_packer(actor, job_id)
        try:
            pdf = packing_repo.read_job_pdf(job_id, "pallet-sheets")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment_disposition("pallet_sheets.pdf")},
        )
