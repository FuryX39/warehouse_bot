"""HTTP API поставок FBO YM: панель /warehouse и десктоп /api/v1."""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.web.warehouse_assignment_helpers import requested_packer_ids
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.yandex_fbo_api import YandexFboApi, get_configured_yandex_fbo_api
from app.yandex_fbo_repository import YandexFboRepository
from app.yandex_fbo_service import create_yandex_fbo_job


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


def _attach_catalog_images(catalog_repo: CatalogRepository, payload: dict[str, Any]) -> None:
    buckets: list[dict[str, Any]] = []
    for key in ("products", "remaining_groups"):
        rows = payload.get(key)
        if isinstance(rows, list):
            buckets.extend(item for item in rows if isinstance(item, dict))
    pids = [
        int(item["catalog_product_id"])
        for item in buckets
        if item.get("catalog_product_id")
    ]
    urls = catalog_repo.image_urls_by_product_ids(pids) if pids else {}
    for item in buckets:
        pid = item.get("catalog_product_id")
        if pid:
            item["image_url"] = urls.get(int(pid), "") or item.get("image_url") or ""


def register_warehouse_yandex_fbo_routes(
    app,
    packing_repo: YandexFboRepository,
    catalog_repo: CatalogRepository,
    users_repo: WarehouseUsersRepository,
    settings: Settings | None,
    require_warehouse_user,
    require_tasks_access,
    *,
    include_manager: bool = True,
    packer_prefixes: tuple[str, ...] | None = None,
    api_factory: Callable[[], YandexFboApi] | None = None,
) -> None:
    def _api() -> YandexFboApi:
        if api_factory is not None:
            api = api_factory()
        else:
            api = get_configured_yandex_fbo_api(settings)
        if api is None or not api.is_configured():
            raise HTTPException(status_code=400, detail="Yandex Market не настроен")
        return api

    def _names() -> dict[int, str]:
        return {
            int(item["id"]): str(item.get("display_name") or item.get("login") or item["id"])
            for item in users_repo.list_assignee_picker()
        }

    def _packer_job_payload(job_id: int) -> dict[str, Any]:
        job = packing_repo.get_job(int(job_id), include_details=True)
        if job is None:
            raise ValueError("Задание не найдено")
        payload = packing_repo.job_to_dict(job, include_details=True)
        _attach_catalog_images(catalog_repo, payload)
        return payload

    def _require_packer(actor: TasksApiActor, job_id: int) -> None:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        if not packing_repo.user_can_pack(int(job_id), int(actor.user.id)):
            raise HTTPException(status_code=403, detail="Задание назначено другому упаковщику")

    def _resolve_barcode(job_id: int, barcode: str) -> dict[str, Any]:
        try:
            return packing_repo.resolve_scan(int(job_id), barcode)
        except ValueError as exc:
            missed = str(exc)
            catalog_row = catalog_repo.find_product_by_barcode(barcode)
            if catalog_row is None:
                raise ValueError(missed) from exc
            job = packing_repo.get_job(int(job_id), include_details=True)
            if job is None:
                raise ValueError("Задание не найдено") from exc
            product = packing_repo.match_catalog_product(
                job,
                catalog_product_id=int(catalog_row.id),
                sku=str(catalog_row.sku or ""),
            )
            if product is None:
                raise ValueError(missed) from exc
            return {"kind": "product", "product": packing_repo._product_dict(product)}

    if include_manager:

        @app.get("/api/warehouse/marketplaces/yandex-fbo/meta")
        async def api_yandex_fbo_meta(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            return {"assignees": users_repo.list_assignee_picker()}

        @app.get("/api/warehouse/marketplaces/yandex-fbo/supplies")
        async def api_yandex_fbo_supplies(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                supplies = await asyncio.to_thread(_api().list_child_supplies)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            jobs = packing_repo.list_jobs(packer_names=_names())
            by_request = {int(job.request_id): packing_repo.job_to_dict(job) for job in jobs}
            rows = []
            for item in supplies:
                request_id = int(item["request_id"])
                job = by_request.get(request_id)
                row = dict(item)
                row["job"] = job
                row["has_job"] = job is not None
                rows.append(row)
            return {"supplies": rows}

        @app.get("/api/warehouse/marketplaces/yandex-fbo/supplies/{request_id}")
        async def api_yandex_fbo_supply(
            request_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                supply = await asyncio.to_thread(_api().get_supply, int(request_id))
                items = await asyncio.to_thread(_api().get_items, int(request_id))
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            job = packing_repo.find_active_by_request_id(int(request_id))
            return {
                "supply": supply,
                "items": items,
                "job": packing_repo.job_to_dict(job) if job else None,
            }

        @app.get("/api/warehouse/marketplaces/yandex-fbo/jobs")
        async def api_yandex_fbo_jobs(
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            jobs = packing_repo.list_jobs(packer_names=_names())
            return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

        @app.post("/api/warehouse/marketplaces/yandex-fbo/jobs")
        async def api_yandex_fbo_jobs_create(
            body: dict[str, Any],
            user: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                request_id = int(body.get("request_id") or 0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Укажите request_id") from exc
            if request_id <= 0:
                raise HTTPException(status_code=400, detail="Укажите request_id")
            try:
                packer_ids = requested_packer_ids(body, users_repo)
                job = await asyncio.to_thread(
                    create_yandex_fbo_job,
                    api=_api(),
                    catalog=catalog_repo,
                    packing_repo=packing_repo,
                    request_id=request_id,
                    packer_user_ids=packer_ids,
                    created_by_user_id=int(user.id),
                )
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": packing_repo.job_to_dict(job, include_details=True)}

        @app.put("/api/warehouse/marketplaces/yandex-fbo/jobs/{job_id}/assignees")
        async def api_yandex_fbo_assignees(
            job_id: int,
            body: dict[str, Any],
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            try:
                ids = requested_packer_ids(body, users_repo)
                job = packing_repo.set_assignees(int(job_id), ids)
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return {"job": packing_repo.job_to_dict(job)}

        @app.post("/api/warehouse/marketplaces/yandex-fbo/jobs/{job_id}/cancel")
        async def api_yandex_fbo_cancel(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> dict:
            job = packing_repo.cancel_job(int(job_id))
            if job is None:
                raise HTTPException(status_code=404, detail="Задание не найдено")
            return {"job": packing_repo.job_to_dict(job)}

        @app.get("/api/warehouse/marketplaces/yandex-fbo/jobs/{job_id}/labels.pdf")
        async def api_yandex_fbo_manager_labels(
            job_id: int,
            _: WarehouseUserRow = Depends(require_warehouse_user),
        ) -> Response:
            try:
                pdf = packing_repo.read_job_labels_pdf(int(job_id))
            except ValueError as exc:
                raise _http_value_error(exc) from exc
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": _attachment_disposition(
                        f"yandex-fbo-{job_id}-cargo-units.pdf"
                    )
                },
            )

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
            resolve_barcode=_resolve_barcode,
        )


def _register_packer_prefix(
    app,
    prefix: str,
    packing_repo: YandexFboRepository,
    catalog_repo: CatalogRepository,
    auth_dep,
    *,
    v1: bool,
    require_packer,
    packer_job_payload,
    resolve_barcode,
) -> None:
    tag = "v1" if v1 else "wh"
    if v1:

        def _actor(actor: TasksApiActor = Depends(auth_dep)) -> TasksApiActor:
            return actor

    else:

        def _actor(user: WarehouseUserRow = Depends(auth_dep)) -> TasksApiActor:
            return TasksApiActor(user=user, via_api_token=False)

    @app.get(f"{prefix}/my", name=f"yandex_fbo_packing_my_{tag}")
    async def api_yandex_fbo_my(actor: TasksApiActor = Depends(_actor)) -> dict:
        if actor.user is None:
            raise HTTPException(
                status_code=400,
                detail="Нужен вход пользователем склада, не общий API-токен",
            )
        jobs = await asyncio.to_thread(packing_repo.list_my_jobs, int(actor.user.id))
        return {"jobs": [packing_repo.job_to_dict(job) for job in jobs]}

    @app.get(f"{prefix}/jobs/{{job_id}}/pack", name=f"yandex_fbo_packing_pack_{tag}")
    async def api_yandex_fbo_pack(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        require_packer(actor, job_id)
        try:
            job = await asyncio.to_thread(packer_job_payload, job_id)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"job": job}

    @app.post(f"{prefix}/jobs/{{job_id}}/resolve", name=f"yandex_fbo_packing_resolve_{tag}")
    async def api_yandex_fbo_resolve(
        job_id: int,
        body: dict[str, Any],
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        require_packer(actor, job_id)
        barcode = str((body or {}).get("barcode") or "").strip()
        try:
            resolved = await asyncio.to_thread(resolve_barcode, job_id, barcode)
            job = await asyncio.to_thread(packer_job_payload, job_id)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        if resolved.get("kind") == "product":
            _attach_catalog_images(catalog_repo, {"products": [resolved["product"]]})
        return {"job": job, **resolved}

    @app.post(f"{prefix}/jobs/{{job_id}}/assign", name=f"yandex_fbo_packing_assign_{tag}")
    async def api_yandex_fbo_assign(
        job_id: int,
        body: dict[str, Any],
        actor: TasksApiActor = Depends(_actor),
    ) -> dict:
        require_packer(actor, job_id)
        payload = body if isinstance(body, dict) else {}
        try:
            product_id = int(payload.get("product_id") or 0)
            quantity = int(payload.get("quantity") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Укажите товар и количество") from exc
        cargo_code = str(payload.get("cargo_code") or "").strip()
        try:
            packing_repo.assign(
                int(job_id),
                int(actor.user.id),
                product_id=product_id,
                quantity=quantity,
                cargo_code=cargo_code,
            )
            job = await asyncio.to_thread(packer_job_payload, job_id)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return {"job": job}

    @app.get(f"{prefix}/jobs/{{job_id}}/labels.pdf", name=f"yandex_fbo_packing_labels_{tag}")
    async def api_yandex_fbo_labels(
        job_id: int,
        actor: TasksApiActor = Depends(_actor),
    ) -> Response:
        require_packer(actor, job_id)
        try:
            pdf = packing_repo.read_job_labels_pdf(int(job_id))
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": _attachment_disposition(
                    f"yandex-fbo-{job_id}-cargo-units.pdf"
                )
            },
        )
