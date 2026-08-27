"""HTTP API заданий FBS-упаковки: панель /warehouse и десктоп /api/v1."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import quote

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.fbs_labels_common import build_labels_zip
from app.fbs_packing_repository import FbsPackingRepository, MARKETPLACE_WB
from app.fbs_packing_service import (
    _bool,
    build_packing_marking_xlsx,
    create_wb_packing_job,
    create_yandex_packing_job,
    list_rows_payload,
    product_has_marking_gtin,
    resolve_packing_scan,
)
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.yandex_fbs_labels import (
    get_configured_yandex_adapter,
    load_yandex_fbs_list_rows,
    normalize_yandex_fbs_substatus,
)
from app.wb_fbs_labels import (
    get_configured_wb_adapter,
    list_rows_payload as wb_list_rows_payload,
    load_wb_fbs_list_rows,
    normalize_wb_fbs_substatus,
)


def _attachment_disposition(filename: str) -> str:
    raw = (filename or "labels.pdf").strip() or "labels.pdf"
    ascii_name = "".join(
        ch if ord(ch) < 128 and (ch.isalnum() or ch in "._-") else "_" for ch in raw
    )
    ascii_name = ascii_name.strip("._") or "labels.pdf"
    utf8_name = quote(raw)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


def _http_value_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _attach_catalog_images(catalog_repo: CatalogRepository, payload: dict[str, Any]) -> None:
    """Проставляет image_url из каталога в remaining_groups и строки задания."""
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
    pids: list[int] = []
    skus: list[str] = []
    for item in buckets:
        pid = item.get("product_id")
        if pid not in (None, ""):
            try:
                pids.append(int(pid))
            except (TypeError, ValueError):
                pass
        sku = str(item.get("sku") or "").strip()
        if sku:
            skus.append(sku)
    by_id = catalog_repo.image_urls_by_product_ids(pids) if pids else {}
    by_sku = catalog_repo.lookup_products_by_skus(skus) if skus else {}
    for item in buckets:
        url = ""
        pid = item.get("product_id")
        if pid not in (None, ""):
            try:
                url = by_id.get(int(pid), "") or ""
            except (TypeError, ValueError):
                url = ""
        if not url:
            sku_row = by_sku.get(str(item.get("sku") or "").strip().casefold()) or {}
            url = str(sku_row.get("image_url") or "")
        item["image_url"] = url


def register_warehouse_fbs_packing_routes(
    app,
    packing_repo: FbsPackingRepository,
    catalog_repo: CatalogRepository,
    users_repo: WarehouseUsersRepository,
    settings: Settings,
    coordinator,
    require_fbs_access,
    require_warehouse_user,
    require_tasks_access,
    *,
    include_manager: bool = True,
    packer_prefixes: tuple[str, ...] | None = None,
) -> None:
    if include_manager:
        @app.get("/api/warehouse/fbs-packing/meta")
        async def api_fbs_packing_meta(
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            return {"assignees": users_repo.list_assignee_picker()}

        @app.get("/api/warehouse/fbs-packing/preview")
        async def api_fbs_packing_preview(
            item_limit: int | None = None,
            order_substatus: str = "STARTED",
            build_list: str = "1",
            marketplace: str = "yandex",
            supply_id: str = "",
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            mp = str(marketplace or "yandex").strip().lower()
            if mp == MARKETPLACE_WB:
                try:
                    order_substatus = normalize_wb_fbs_substatus(order_substatus)
                except ValueError as exc:
                    raise _http_value_error(exc) from exc
                adapter = get_configured_wb_adapter(coordinator)
                if adapter is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Wildberries API не настроен (WB_API_TOKEN)",
                    )

                def _run_wb():
                    return load_wb_fbs_list_rows(
                        adapter,
                        substatus=order_substatus,
                        supply_id=supply_id,
                        max_units=item_limit,
                    )

                try:
                    list_rows, orders, warnings, available = await asyncio.to_thread(_run_wb)
                except ValueError as exc:
                    raise _http_value_error(exc) from exc
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=502, detail=f"Wildberries API: {exc}") from exc
                return {
                    "marketplace": mp,
                    "count": len(list_rows),
                    "available_count": available,
                    "orders_count": len(orders),
                    "substatus": order_substatus,
                    "warnings": warnings,
                    "list_rows": wb_list_rows_payload(list_rows),
                }

            try:
                order_substatus = normalize_yandex_fbs_substatus(order_substatus)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            adapter = get_configured_yandex_adapter(coordinator)
            if adapter is None:
                raise HTTPException(
                    status_code=400,
                    detail="Yandex Market API не настроен (YANDEX_CAMPAIGN_ID / YANDEX_API_KEY)",
                )

            def _run():
                return load_yandex_fbs_list_rows(
                    adapter,
                    substatus=order_substatus,
                    default_stocks_sheet_url=settings.default_stocks_sheet_url,
                    google_service_account_file=settings.google_service_account_file,
                    fbs_assembly_sheet_name=settings.fbs_assembly_sheet_name,
                    assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
                    max_units=item_limit,
                    apply_assembly=_bool(build_list, True),
                )

            try:
                list_rows, orders, warnings, available = await asyncio.to_thread(_run)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Yandex API: {exc}") from exc
            return {
                "marketplace": mp,
                "count": len(list_rows),
                "available_count": available,
                "orders_count": len(orders),
                "substatus": order_substatus,
                "warnings": warnings,
                "list_rows": list_rows_payload(list_rows, orders),
            }

        @app.get("/api/warehouse/fbs-packing/wb/supplies")
        async def api_fbs_packing_wb_supplies(
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            adapter = get_configured_wb_adapter(coordinator)
            if adapter is None:
                raise HTTPException(
                    status_code=400,
                    detail="Wildberries API не настроен (WB_API_TOKEN)",
                )

            def _run():
                return adapter.list_open_supplies()

            try:
                supplies = await asyncio.to_thread(_run)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Wildberries API: {exc}") from exc
            items = [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "done": bool(item.get("done")),
                }
                for item in supplies
                if str(item.get("id") or "").strip()
            ]
            return {"supplies": items}

        @app.get("/api/warehouse/fbs-packing/jobs")
        async def api_fbs_packing_jobs_list(
            marketplace: str | None = None,
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            names = {
                int(item["id"]): str(item["display_name"])
                for item in users_repo.list_assignee_picker()
            }
            mp = str(marketplace or "").strip().lower() or None
            jobs = packing_repo.list_jobs(packer_names=names, marketplace=mp)
            return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

        @app.post("/api/warehouse/fbs-packing/jobs")
        async def api_fbs_packing_jobs_create(
            body: dict,
            user: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            payload = body if isinstance(body, dict) else {}
            mp = str(payload.get("marketplace") or "yandex").strip().lower()
            raw_ids = payload.get("packer_user_ids") or []
            if not isinstance(raw_ids, list) or not raw_ids:
                raise HTTPException(status_code=400, detail="Назначьте хотя бы одного упаковщика")
            item_limit = payload.get("item_limit")
            try:
                limit = int(item_limit) if item_limit not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Некорректный лимит товаров") from exc

            if mp == MARKETPLACE_WB:
                adapter = get_configured_wb_adapter(coordinator)
                if adapter is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Wildberries API не настроен (WB_API_TOKEN)",
                    )
                try:
                    substatus = normalize_wb_fbs_substatus(payload.get("order_substatus"))
                except ValueError as exc:
                    raise _http_value_error(exc) from exc
                supply_id = str(payload.get("supply_id") or "").strip()

                def _run_wb():
                    return create_wb_packing_job(
                        adapter=adapter,
                        catalog=catalog_repo,
                        packing_repo=packing_repo,
                        order_substatus=substatus,
                        require_cis=_bool(payload.get("require_cis"), False),
                        item_limit=limit,
                        packer_user_ids=raw_ids,
                        created_by_user_id=int(user.id) if user else None,
                        supply_id=supply_id,
                    )

                try:
                    job = await asyncio.to_thread(_run_wb)
                except ValueError as exc:
                    raise _http_value_error(exc) from exc
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                return {"job": packing_repo.job_to_dict(job, include_lines=True)}

            adapter = get_configured_yandex_adapter(coordinator)
            if adapter is None:
                raise HTTPException(
                    status_code=400,
                    detail="Yandex Market API не настроен (YANDEX_CAMPAIGN_ID / YANDEX_API_KEY)",
                )
            try:
                substatus = normalize_yandex_fbs_substatus(payload.get("order_substatus"))
            except ValueError as exc:
                raise _http_value_error(exc) from exc

            def _run():
                return create_yandex_packing_job(
                    adapter=adapter,
                    catalog=catalog_repo,
                    packing_repo=packing_repo,
                    settings=settings,
                    order_substatus=substatus,
                    build_list=_bool(payload.get("build_list"), True),
                    require_cis=_bool(payload.get("require_cis"), False),
                    item_limit=limit,
                    packer_user_ids=raw_ids,
                    created_by_user_id=int(user.id) if user else None,
                )

            try:
                job = await asyncio.to_thread(_run)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.get("/api/warehouse/fbs-packing/jobs/{job_id}")
        async def api_fbs_packing_job_get(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job, include_lines=True)}

        @app.post("/api/warehouse/fbs-packing/jobs/{job_id}/cancel")
        async def api_fbs_packing_job_cancel(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> dict:
            try:
                job = packing_repo.cancel_job(job_id)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job)}

        @app.get("/api/warehouse/fbs-packing/jobs/{job_id}/labels")
        async def api_fbs_packing_job_labels(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> Response:
            try:
                pdf = packing_repo.read_merged_pdf(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": _attachment_disposition("yandex_fbs_labels.pdf")},
            )

        @app.get("/api/warehouse/fbs-packing/jobs/{job_id}/marking.xlsx")
        async def api_fbs_packing_job_marking_xlsx(
            job_id: int,
            _: WarehouseUserRow | None = Depends(require_fbs_access),
        ) -> Response:
            job = packing_repo.get_job(job_id, include_lines=True)
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            try:
                content = await asyncio.to_thread(build_packing_marking_xlsx, job)
            except ImportError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Не установлен openpyxl: pip install openpyxl",
                ) from exc
            return Response(
                content=content,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": _attachment_disposition(f"fbs_marking_{job_id}.xlsx")
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
        _attach_catalog_images(catalog_repo, payload)
        return payload

    if packer_prefixes is None:
        packer_prefixes = ("/api/warehouse/fbs-packing", "/api/v1/fbs-packing")
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
    packing_repo: FbsPackingRepository,
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

    @app.get(f"{prefix}/my", name=f"fbs_packing_my_{tag}")
    async def api_fbs_packing_my(actor: TasksApiActor = Depends(_actor)) -> dict:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        jobs = await asyncio.to_thread(packing_repo.list_my_jobs, int(actor.user.id))
        return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

    @app.get(f"{prefix}/jobs/{{job_id}}/pack", name=f"fbs_packing_pack_{tag}")
    async def api_fbs_packing_pack_get(
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
        batch: bool = False,
        cis_raw: str = "",
        cis_key: str = "",
        cis_gtin: str = "",
        include_pdf: bool = True,
        auto_close: bool = False,
    ) -> dict:
        lines = packing_repo.allocate_lines(
            job_id,
            user_id,
            sku=sku,
            product_id=product_id,
            batch=batch,
            cis_raw=cis_raw,
            cis_key=cis_key,
            cis_gtin=cis_gtin,
            auto_close=auto_close,
        )
        pdfs_b64: list[str] = []
        if include_pdf:
            for line in lines:
                pdf = packing_repo.read_line_pdf(job_id, line.id)
                pdfs_b64.append(base64.b64encode(pdf).decode("ascii"))
        job = packer_job_payload(job_id)
        return {
            "line": packing_repo.line_to_dict(lines[0]),
            "lines": [packing_repo.line_to_dict(line) for line in lines],
            "job": job,
            "pdf_base64": pdfs_b64[0] if pdfs_b64 else "",
            "pdfs_base64": pdfs_b64,
        }

    def _scan_product_sync(
        job_id: int,
        user_id: int,
        barcode: str,
        *,
        batch: bool,
        include_pdf: bool = True,
        auto_close: bool = False,
    ) -> dict:
        job = packing_repo.get_job(job_id)
        if job is None:
            raise ValueError("Задание не найдено")
        resolved = resolve_packing_scan(catalog_repo, barcode)
        if (
            job.require_cis
            and not resolved.is_cis
            and product_has_marking_gtin(catalog_repo, resolved.product_id)
        ):
            raise ValueError("Нужен КИЗ (Data Matrix), обычный штрихкод не принимается")
        use_batch = bool(batch) and not resolved.is_cis
        return _allocate_response(
            job_id,
            user_id,
            resolved.sku,
            resolved.product_id,
            batch=use_batch,
            cis_raw=resolved.cis_raw,
            cis_key=resolved.cis_key,
            cis_gtin=resolved.cis_gtin,
            include_pdf=include_pdf,
            auto_close=auto_close,
        )

    def _batch_flag(body: dict | None) -> bool:
        raw = (body or {}).get("batch")
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    def _auto_close_flag(body: dict | None) -> bool:
        if not isinstance(body, dict):
            return False
        raw = body.get("auto_close")
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    def _include_pdf_flag(body: dict | None) -> bool:
        if not isinstance(body, dict) or "include_pdf" not in body:
            return True
        raw = body.get("include_pdf")
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() not in {"0", "false", "no", "off"}

    @app.post(f"{prefix}/jobs/{{job_id}}/scan-product", name=f"fbs_packing_scan_product_{tag}")
    async def api_fbs_packing_scan_product(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        barcode = str((body or {}).get("barcode") or (body or {}).get("code") or "")
        payload = body if isinstance(body, dict) else {}
        batch = _batch_flag(payload)
        include_pdf = _include_pdf_flag(payload)
        auto_close = _auto_close_flag(payload)
        try:
            return await asyncio.to_thread(
                _scan_product_sync,
                job_id,
                user_id,
                barcode,
                batch=batch,
                include_pdf=include_pdf,
                auto_close=auto_close,
            )
        except ValueError as exc:
            raise _http_value_error(exc) from exc

    @app.post(f"{prefix}/jobs/{{job_id}}/pick-sku", name=f"fbs_packing_pick_sku_{tag}")
    async def api_fbs_packing_pick_sku(
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
        batch = _batch_flag(payload)
        include_pdf = _include_pdf_flag(payload)
        auto_close = _auto_close_flag(payload)
        try:
            job = packing_repo.get_job(job_id)
            if job is None:
                raise ValueError("Задание не найдено")
            if job.require_cis and product_has_marking_gtin(catalog_repo, product_id):
                raise ValueError("Для маркируемого товара нужен пик КИЗ, не ручной выбор")
            return await asyncio.to_thread(
                _allocate_response,
                job_id,
                user_id,
                sku,
                product_id,
                batch=batch,
                include_pdf=include_pdf,
                auto_close=auto_close,
            )
        except ValueError as exc:
            raise _http_value_error(exc) from exc

    @app.get(f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/label", name=f"fbs_packing_line_label_{tag}")
    async def api_fbs_packing_line_label(
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
            headers={"Content-Disposition": _attachment_disposition(f"fbs_line_{line_id}.pdf")},
        )

    @app.get(f"{prefix}/jobs/{{job_id}}/line-labels.zip", name=f"fbs_packing_line_labels_zip_{tag}")
    async def api_fbs_packing_line_labels_zip(
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
            headers={"Content-Disposition": _attachment_disposition(f"fbs_job_{job_id}_labels.zip")},
        )

    @app.post(f"{prefix}/jobs/{{job_id}}/scan-label", name=f"fbs_packing_scan_label_{tag}")
    async def api_fbs_packing_scan_label(
        job_id: int,
        body: dict,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        user_id = require_packer(actor, job_id)
        code = str((body or {}).get("barcode") or (body or {}).get("code") or "")

        def _run():
            line = packing_repo.scan_label(job_id, user_id, code)
            return packing_repo.line_to_dict(line), packer_job_payload(job_id)

        try:
            line, job = await asyncio.to_thread(_run)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"line": line, "job": job}

    @app.post(f"{prefix}/jobs/{{job_id}}/lines/{{line_id}}/close", name=f"fbs_packing_line_close_{tag}")
    async def api_fbs_packing_line_close(
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
        name=f"fbs_packing_line_cancel_{tag}",
    )
    async def api_fbs_packing_line_cancel(
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
        name=f"fbs_packing_line_set_status_{tag}",
    )
    async def api_fbs_packing_line_set_status(
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
