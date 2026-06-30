"""HTTP API FBO-поставок Ozon для вкладки «Маркетплейсы»."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from app.adapters.ozon import OzonAdapter
from app.fbs_labels_common import merge_label_pdfs
from app.ozon_fbo_api import (
    DELIVERY_CROSSDOCK,
    build_cargoes_create_payload,
    create_draft,
    create_supply_from_draft,
    draft_create_info,
    draft_warehouse_error_detail,
    poll_draft_create_info,
    draft_timeslots,
    expand_order_supplies,
    extract_draft_id,
    fetch_analytics_stocks,
    fetch_supply_orders_overview,
    get_bundle_items,
    get_supply_orders,
    inner_supply_id_from_order,
    list_macrolocal_clusters,
    normalize_supply_order,
    parse_timeslot_days,
    poll_cargoes_create_info,
    poll_supply_create_status,
    rank_demand_by_clusters,
    resolve_inner_supply_id,
    resolve_offer_ids,
    dropoff_presets_from_env,
    search_dropoff_warehouses,
    top_warehouse_from_draft_info,
)
from app.ozon_fbo_supply_repository import (
    BATCH_STATUS_PACKING,
    BATCH_STATUS_SUBMITTED,
    STATUS_ASSIGNED,
    STATUS_LABELS_READY,
    STATUS_READY,
    STATUS_SENT_TO_OZON,
    OzonFboSupplyRepository,
    batch_to_dict,
    supply_to_dict,
)
from app.warehouse_users_repository import WarehouseUserRow


def _filters_from_query(params: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("q", "status", "assigned_user_id"):
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out


def register_warehouse_ozon_fbo_routes(
    app,
    fbo_repo: OzonFboSupplyRepository,
    require_warehouse_user,
    ozon_adapter: OzonAdapter | None = None,
) -> None:
    @app.get("/api/warehouse/marketplaces/ozon-fbo/meta")
    async def api_ozon_fbo_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return {
            "assignees": fbo_repo.assignees(),
            "supply_kinds": [
                {"id": "pallet", "name": "Паллеты"},
                {"id": "box", "name": "Короба"},
            ],
            "delivery_types": [
                {"id": "direct", "name": "Самостоятельно"},
                {"id": "crossdock", "name": "Кросс-док"},
            ],
            "batch_statuses": [
                {"id": "planning", "name": "Планирование"},
                {"id": "submitted", "name": "Создано в Ozon"},
                {"id": "packing", "name": "Сборка грузомест"},
                {"id": "done", "name": "Завершён"},
            ],
            "statuses": [
                {"id": "draft", "name": "Черновик"},
                {"id": "assigned", "name": "Назначена"},
                {"id": "packing", "name": "Сборка"},
                {"id": "ready", "name": "Готова к Ozon"},
                {"id": "sent_to_ozon", "name": "Отправлена в Ozon"},
                {"id": "labels_ready", "name": "Этикетки готовы"},
                {"id": "done", "name": "Завершена"},
            ],
            "dropoff_presets": dropoff_presets_from_env(),
        }

    def _extract_first_id(data: dict, keys: tuple[str, ...]) -> str:
        stack: list[Any] = [data]
        while stack:
            cur = stack.pop(0)
            if isinstance(cur, dict):
                for key in keys:
                    val = cur.get(key)
                    if val is not None and str(val).strip():
                        return str(val).strip()
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
        return ""

    def _warehouse_id(raw: str) -> int | str:
        value = str(raw or "").strip()
        if not value:
            raise ValueError("Укажите склад Ozon")
        try:
            return int(value)
        except ValueError:
            return value

    def _draft_payload(supply: dict) -> dict:
        items = []
        for item in supply.get("items") or []:
            sku = str(item.get("sku") or "").strip()
            qty = int(item.get("quantity") or 0)
            if sku and qty > 0:
                items.append({"sku": sku, "quantity": qty})
        if not items:
            raise ValueError("Добавьте товары в заявку")
        return {
            "supply_type": "DIRECT",
            "warehouse_id": _warehouse_id(supply.get("ozon_warehouse_id") or supply.get("ozon_cluster_id")),
            "items": items,
        }

    def _ozon() -> OzonAdapter:
        if ozon_adapter is None or not ozon_adapter.is_configured():
            raise HTTPException(
                status_code=400,
                detail="Ozon API не настроен: задайте OZON_CLIENT_ID и OZON_API_KEY",
            )
        return ozon_adapter

    def _send_supply_cargoes_to_ozon(supply_id: int, supply: dict[str, Any]) -> dict[str, Any]:
        ozon = _ozon()
        inner_id = resolve_inner_supply_id(ozon, supply)
        order_raw = str(supply.get("ozon_order_id") or "").strip()
        inner_raw = str(supply.get("ozon_supply_id") or "").strip()
        if str(inner_id) != inner_raw or not order_raw:
            patch: dict[str, str] = {"ozon_supply_id": str(inner_id)}
            if not order_raw and inner_raw and inner_raw != str(inner_id):
                patch["ozon_order_id"] = inner_raw
            elif order_raw:
                patch["ozon_order_id"] = order_raw
            fbo_repo.update_supply(supply_id, patch)
        payload = build_cargoes_create_payload(supply, inner_supply_id=inner_id)
        data = ozon.fbo_cargoes_create(payload)
        operation_id = _extract_first_id(data, ("operation_id", "operationId", "task_id", "taskId"))
        if not operation_id:
            raise ValueError("Ozon не вернул operation_id для грузомест")
        info = poll_cargoes_create_info(ozon, operation_id)
        status = str(info.get("status") or "").upper()
        if status == "FAILED" or status == "ERROR":
            errors = info.get("errors") or (info.get("result") or {}).get("errors") or []
            raise ValueError(f"Ozon отклонил грузоместа: {errors or info}")
        fbo_repo.update_supply(
            supply_id,
            {"status": STATUS_SENT_TO_OZON, "cargoes_operation_id": operation_id},
        )
        updated = fbo_repo.apply_ozon_cargo_ids(supply_id, info)
        return {
            "operation_id": operation_id,
            "inner_supply_id": inner_id,
            "payload": payload,
            "data": data,
            "info": info,
            "supply": supply_to_dict(updated, include_details=True) if updated else supply,
        }

    def _inner_supply_id_for_labels(supply: dict[str, Any]) -> int:
        return resolve_inner_supply_id(_ozon(), supply)

    def _supply_payload(supply: dict, body: dict) -> dict:
        draft_id = str(body.get("draft_id") or supply.get("ozon_draft_id") or "").strip()
        timeslot_id = str(body.get("timeslot_id") or "").strip()
        if not draft_id:
            raise ValueError("У заявки нет ID черновика Ozon")
        if not timeslot_id:
            raise ValueError("Укажите timeslot_id")
        return {"draft_id": draft_id, "timeslot_id": timeslot_id}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/supplies")
    async def api_ozon_fbo_supplies(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = fbo_repo.list_supplies(_filters_from_query(request.query_params))
        return {"supplies": [supply_to_dict(r, include_details=False) for r in rows]}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/clusters")
    async def api_ozon_fbo_clusters(
        body: dict | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            data = _ozon().fbo_cluster_list(body or {})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": data}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/warehouses")
    async def api_ozon_fbo_warehouses(
        body: dict | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            data = _ozon().fbo_warehouse_list(body or {})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": data}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/timeslots")
    async def api_ozon_fbo_timeslots(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            data = _ozon().fbo_timeslot_info(body or {})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": data}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/my-supplies")
    async def api_ozon_fbo_my_supplies(
        request: Request,
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        filters["assigned_user_id"] = str(user.id)
        rows = fbo_repo.list_supplies(filters)
        return {"supplies": [supply_to_dict(r, include_details=False) for r in rows]}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}")
    async def api_ozon_fbo_get_supply(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        return {"supply": supply_to_dict(row, include_details=True)}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies")
    async def api_ozon_fbo_create_supply(
        body: dict,
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = fbo_repo.create_supply(body, manager_user_id=user.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"supply": supply_to_dict(row, include_details=True)}

    @app.put("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}")
    async def api_ozon_fbo_update_supply(
        supply_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            row = fbo_repo.update_supply(supply_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        return {"supply": supply_to_dict(row, include_details=True)}

    @app.delete("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}")
    async def api_ozon_fbo_delete_supply(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        if not fbo_repo.delete_supply(supply_id):
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        return {"ok": True}

    @app.put("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/cargoes")
    async def api_ozon_fbo_save_cargoes(
        supply_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        cargoes = body.get("cargoes")
        if not isinstance(cargoes, list):
            raise HTTPException(status_code=400, detail="cargoes должен быть массивом")
        try:
            row = fbo_repo.save_cargoes(supply_id, cargoes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        return {"supply": supply_to_dict(row, include_details=True)}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/draft")
    async def api_ozon_fbo_create_draft(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        try:
            payload = _draft_payload(supply)
            data = _ozon().fbo_draft_create(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        draft_id = _extract_first_id(data, ("draft_id", "draftId", "id"))
        if draft_id:
            fbo_repo.update_supply(supply_id, {"ozon_draft_id": draft_id})
        return {"ok": True, "payload": payload, "data": data, "draft_id": draft_id}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/supply")
    async def api_ozon_fbo_create_supply_from_draft(
        supply_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        try:
            payload = _supply_payload(supply, body or {})
            data = _ozon().fbo_draft_supply_create(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        ozon_supply_id = _extract_first_id(data, ("supply_id", "supply_order_id", "order_id", "id"))
        if ozon_supply_id:
            fbo_repo.update_supply(supply_id, {"ozon_supply_id": ozon_supply_id})
        return {"ok": True, "payload": payload, "data": data, "ozon_supply_id": ozon_supply_id}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/cargoes")
    async def api_ozon_fbo_send_cargoes(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        try:
            result = _send_supply_cargoes_to_ozon(supply_id, supply)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/cargoes/status")
    async def api_ozon_fbo_cargoes_status(
        supply_id: int,
        body: dict | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        payload = dict(body or {})
        if "operation_id" not in payload and row.cargoes_operation_id:
            payload["operation_id"] = row.cargoes_operation_id
        if not payload:
            raise HTTPException(status_code=400, detail="Нет operation_id для проверки")
        try:
            data = _ozon().fbo_cargoes_create_info(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        status = str(data.get("status") or "").upper()
        if status == "SUCCESS":
            fbo_repo.apply_ozon_cargo_ids(supply_id, data)
        return {"ok": True, "payload": payload, "data": data}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/cargoes/rules")
    async def api_ozon_fbo_cargoes_rules(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        try:
            inner_id = resolve_inner_supply_id(_ozon(), supply)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = {"supply_ids": [inner_id]}
        try:
            data = _ozon().fbo_cargoes_rules_get(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "payload": payload, "data": data}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/labels")
    async def api_ozon_fbo_create_labels(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        try:
            inner_id = _inner_supply_id_for_labels(supply)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cargo_ids = [
            str(c.get("ozon_cargo_id") or "").strip()
            for c in supply.get("cargoes") or []
            if str(c.get("ozon_cargo_id") or "").strip()
        ]
        payload: dict[str, Any] = {"supply_id": inner_id}
        if cargo_ids:
            payload["cargo_ids"] = cargo_ids
        try:
            data = _ozon().fbo_cargo_labels_create(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        file_guid = _extract_first_id(data, ("file_guid", "fileGuid", "guid"))
        operation_id = _extract_first_id(data, ("operation_id", "operationId", "task_id", "taskId"))
        if file_guid:
            fbo_repo.update_supply(
                supply_id,
                {
                    "labels_file_guid": file_guid,
                    "labels_operation_id": operation_id,
                    "labels_filename": f"ozon_fbo_cargo_labels_{inner_id}.pdf",
                    "status": STATUS_LABELS_READY,
                },
            )
        else:
            fbo_repo.update_supply(
                supply_id,
                {"status": STATUS_READY, "labels_operation_id": operation_id},
            )
        return {
            "ok": True,
            "payload": payload,
            "data": data,
            "file_guid": file_guid,
            "operation_id": operation_id,
        }

    @app.post("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/ozon/labels/status")
    async def api_ozon_fbo_labels_status(
        supply_id: int,
        body: dict | None = None,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        payload = dict(body or {})
        if "operation_id" not in payload and row.labels_operation_id:
            payload["operation_id"] = row.labels_operation_id
        if "file_guid" not in payload and row.labels_file_guid:
            payload["file_guid"] = row.labels_file_guid
        if not payload:
            raise HTTPException(status_code=400, detail="Нет operation_id или file_guid для проверки")
        try:
            data = _ozon().fbo_cargo_labels_get(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        file_guid = _extract_first_id(data, ("file_guid", "fileGuid", "guid"))
        if file_guid:
            fbo_repo.update_supply(
                supply_id,
                {
                    "labels_file_guid": file_guid,
                    "labels_filename": f"ozon_fbo_cargo_labels_{supply_id}.pdf",
                    "status": STATUS_LABELS_READY,
                },
            )
        return {"ok": True, "payload": payload, "data": data, "file_guid": file_guid}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/supplies/{supply_id}/labels.pdf")
    async def api_ozon_fbo_labels_file(
        supply_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        row = fbo_repo.get_supply(supply_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FBO-заявка не найдена")
        supply = supply_to_dict(row, include_details=True)
        file_guid = str(supply.get("labels_file_guid") or "").strip()
        if not file_guid:
            raise HTTPException(status_code=400, detail="Сначала сгенерируйте этикетки")
        try:
            pdf = _ozon().fbo_cargo_labels_file(file_guid)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        filename = supply.get("labels_filename") or f"ozon_fbo_labels_{supply_id}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/warehouse/marketplaces/ozon-fbo/ozon/demand")
    async def api_ozon_fbo_demand(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        offer_id = str(request.query_params.get("offer_id") or "").strip()
        if not offer_id:
            raise HTTPException(status_code=400, detail="Укажите offer_id")
        ozon = _ozon()
        try:
            products = resolve_offer_ids(ozon, [offer_id])
            if not products or not products[0].get("ozon_sku"):
                raise HTTPException(status_code=404, detail="Товар не найден в Ozon")
            ozon_sku = int(products[0]["ozon_sku"])
            clusters_raw = ozon.fbo_cluster_list()
            stocks = fetch_analytics_stocks(ozon, ozon_skus=[ozon_sku])
            ranked = rank_demand_by_clusters(
                stocks,
                clusters_raw,
                offer_id=offer_id,
                ozon_sku=ozon_sku,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "product": products[0],
            "clusters": ranked,
            "macrolocal_clusters": list_macrolocal_clusters(ozon),
        }

    @app.get("/api/warehouse/marketplaces/ozon-fbo/ozon/macrolocal-clusters")
    async def api_ozon_fbo_macrolocal_clusters(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            clusters = list_macrolocal_clusters(_ozon())
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"clusters": clusters}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/dropoff-warehouses")
    async def api_ozon_fbo_dropoff_warehouses(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        query = str(body.get("search") or body.get("q") or "").strip()
        try:
            data = search_dropoff_warehouses(_ozon(), query)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": data.get("raw") or data, "items": data.get("items") or []}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/draft-info")
    async def api_ozon_fbo_draft_info(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        draft_id = body.get("draft_id")
        if not draft_id:
            raise HTTPException(status_code=400, detail="draft_id обязателен")
        try:
            info = draft_create_info(_ozon(), int(draft_id))
            warehouse = top_warehouse_from_draft_info(info)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": info, "warehouse": warehouse}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/timeslots-v2")
    async def api_ozon_fbo_timeslots_v2(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        required = ("draft_id", "delivery_type", "date_from", "date_to", "warehouse")
        missing = [k for k in required if not body.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Не хватает полей: {', '.join(missing)}")
        warehouse = body.get("warehouse")
        if not isinstance(warehouse, dict):
            raise HTTPException(status_code=400, detail="warehouse должен быть объектом")
        try:
            data = draft_timeslots(
                _ozon(),
                draft_id=int(body["draft_id"]),
                warehouse=warehouse,
                delivery_type=str(body["delivery_type"]),
                date_from=str(body["date_from"]),
                date_to=str(body["date_to"]),
            )
            slots = parse_timeslot_days(data)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "data": data, "slots": slots}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/ozon/preview-timeslots")
    async def api_ozon_fbo_preview_timeslots(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        """Черновик для первого кластера — только чтобы показать доступные слоты (без заявки)."""
        delivery_type = str(body.get("delivery_type") or "direct").lower()
        dropoff_id = str(body.get("dropoff_warehouse_id") or "").strip()
        dropoff_type = str(body.get("dropoff_warehouse_type") or "").strip()
        dropoff_name = str(body.get("dropoff_warehouse_name") or "").strip()
        clusters = body.get("clusters") or []
        items = body.get("items") or []
        date_from = str(body.get("date_from") or "").strip()
        date_to = str(body.get("date_to") or "").strip()
        if not clusters:
            raise HTTPException(status_code=400, detail="Выберите кластер")
        if not date_from or not date_to:
            raise HTTPException(status_code=400, detail="Укажите период дат")
        macro_id = clusters[0].get("macrolocal_cluster_id") or clusters[0].get("id")
        if macro_id is None:
            raise HTTPException(status_code=400, detail="Некорректный кластер")
        ozon = _ozon()
        offer_ids = [str(it.get("sku") or it.get("offer_id") or "").strip() for it in items]
        resolved = resolve_offer_ids(ozon, [x for x in offer_ids if x])
        sku_map = {str(p["offer_id"]): p for p in resolved}
        draft_items = []
        for it in items:
            oid = str(it.get("sku") or it.get("offer_id") or "").strip()
            qty = int(it.get("quantity") or 0)
            if not oid or qty <= 0:
                continue
            prod = sku_map.get(oid)
            if prod and prod.get("ozon_sku"):
                draft_items.append({"ozon_sku": int(prod["ozon_sku"]), "quantity": qty})
        if not draft_items:
            raise HTTPException(status_code=400, detail="Добавьте товары")
        try:
            created = create_draft(
                ozon,
                delivery_type=delivery_type,
                macrolocal_cluster_id=int(macro_id),
                items=draft_items,
                dropoff_warehouse_id=int(dropoff_id) if dropoff_id else None,
                dropoff_warehouse_type=dropoff_type or None,
                dropoff_warehouse_name=dropoff_name or None,
            )
            draft_id = extract_draft_id(created.get("response") or {})
            if not draft_id:
                raise HTTPException(status_code=502, detail="Ozon не вернул draft_id")
            info = poll_draft_create_info(ozon, draft_id)
            warehouse = top_warehouse_from_draft_info(info)
            if not warehouse:
                raise HTTPException(status_code=502, detail=draft_warehouse_error_detail(info))
            time.sleep(5)
            data = draft_timeslots(
                ozon,
                draft_id=draft_id,
                warehouse=warehouse,
                delivery_type=delivery_type,
                date_from=date_from,
                date_to=date_to,
            )
            slots = parse_timeslot_days(data)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "ok": True,
            "draft_id": draft_id,
            "warehouse": warehouse,
            "slots": slots,
        }

    @app.get("/api/warehouse/marketplaces/ozon-fbo/batches")
    async def api_ozon_fbo_batches(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = fbo_repo.list_batches(_filters_from_query(request.query_params))
        return {"batches": [batch_to_dict(r, include_details=False) for r in rows]}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/batches/{batch_id}")
    async def api_ozon_fbo_get_batch(
        batch_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = fbo_repo.get_batch(batch_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Пакет FBO не найден")
        return {"batch": batch_to_dict(row, include_details=True)}

    @app.post("/api/warehouse/marketplaces/ozon-fbo/batches/submit")
    async def api_ozon_fbo_batch_submit(
        body: dict,
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        """Создать пакет заявок в Ozon: N кластеров × один таймслот."""
        delivery_type = str(body.get("delivery_type") or "direct").lower()
        dropoff_id = str(body.get("dropoff_warehouse_id") or "").strip()
        dropoff_type = str(body.get("dropoff_warehouse_type") or "").strip()
        dropoff_name = str(body.get("dropoff_warehouse_name") or "").strip()
        timeslot = body.get("timeslot") or {}
        ts_from = str(timeslot.get("from_in_timezone") or body.get("timeslot_from") or "").strip()
        ts_to = str(timeslot.get("to_in_timezone") or body.get("timeslot_to") or "").strip()
        clusters = body.get("clusters") or []
        items = body.get("items") or []
        supply_kind = str(body.get("supply_kind") or "pallet")
        title = str(body.get("title") or "").strip() or "Пакет FBO"

        if delivery_type == DELIVERY_CROSSDOCK and not dropoff_id:
            raise HTTPException(status_code=400, detail="Для кросс-дока выберите точку отгрузки")
        if not ts_from or not ts_to:
            raise HTTPException(status_code=400, detail="Выберите таймслот")
        if not clusters:
            raise HTTPException(status_code=400, detail="Выберите хотя бы один кластер")
        if not items:
            raise HTTPException(status_code=400, detail="Добавьте товары")

        ozon = _ozon()
        offer_ids = [str(it.get("sku") or it.get("offer_id") or "").strip() for it in items]
        offer_ids = [x for x in offer_ids if x]
        try:
            resolved = resolve_offer_ids(ozon, offer_ids)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        sku_map = {str(p["offer_id"]): p for p in resolved}
        draft_items: list[dict[str, Any]] = []
        local_items: list[dict[str, Any]] = []
        for it in items:
            oid = str(it.get("sku") or it.get("offer_id") or "").strip()
            qty = int(it.get("quantity") or 0)
            if not oid or qty <= 0:
                continue
            prod = sku_map.get(oid)
            if not prod or not prod.get("ozon_sku"):
                raise HTTPException(status_code=400, detail=f"Товар {oid!r} не найден в Ozon")
            draft_items.append({"ozon_sku": int(prod["ozon_sku"]), "quantity": qty})
            local_items.append(
                {
                    "sku": oid,
                    "name": prod.get("name") or oid,
                    "quantity": qty,
                    "product_id": it.get("product_id"),
                }
            )
        if not draft_items:
            raise HTTPException(status_code=400, detail="Нет валидных товаров")

        batch = fbo_repo.create_batch(
            {
                "title": title,
                "delivery_type": delivery_type,
                "dropoff_warehouse_id": dropoff_id,
                "dropoff_warehouse_name": dropoff_name,
                "timeslot_from": ts_from,
                "timeslot_to": ts_to,
                "status": BATCH_STATUS_PLANNING,
            },
            manager_user_id=user.id,
        )

        results: list[dict[str, Any]] = []
        dropoff_int = int(dropoff_id) if dropoff_id else None
        for cluster in clusters:
            macro_id = cluster.get("macrolocal_cluster_id") or cluster.get("id")
            cluster_name = str(cluster.get("name") or cluster.get("cluster_name") or "")
            if macro_id is None:
                results.append({"cluster_name": cluster_name, "error": "Нет macrolocal_cluster_id"})
                continue
            row: dict[str, Any] = {"cluster_name": cluster_name, "macrolocal_cluster_id": macro_id}
            try:
                time.sleep(5)
                created = create_draft(
                    ozon,
                    delivery_type=delivery_type,
                    macrolocal_cluster_id=int(macro_id),
                    items=draft_items,
                    dropoff_warehouse_id=dropoff_int,
                    dropoff_warehouse_type=dropoff_type or None,
                    dropoff_warehouse_name=dropoff_name or None,
                )
                draft_id = extract_draft_id(created.get("response") or {})
                row["draft_id"] = draft_id
                if not draft_id:
                    row["error"] = "Ozon не вернул draft_id"
                    results.append(row)
                    continue
                info = poll_draft_create_info(ozon, draft_id)
                warehouse = top_warehouse_from_draft_info(info)
                if not warehouse:
                    row["error"] = draft_warehouse_error_detail(info)
                    results.append(row)
                    continue
                row["warehouse"] = warehouse
                time.sleep(5)
                create_supply_from_draft(
                    ozon,
                    draft_id=draft_id,
                    warehouse=warehouse,
                    delivery_type=delivery_type,
                    timeslot={"from_in_timezone": ts_from, "to_in_timezone": ts_to},
                )
                time.sleep(5)
                st = poll_supply_create_status(ozon, draft_id)
                order_id = st.get("order_id")
                row["supply_order_id"] = order_id
                row["ozon_status"] = st.get("status")
                if not order_id:
                    row["error"] = f"Заявка не создана: {st}"
                    results.append(row)
                    continue
                inner_supply_id = order_id
                try:
                    oz_orders = get_supply_orders(ozon, [int(order_id)])
                    if oz_orders:
                        resolved = inner_supply_id_from_order(
                            oz_orders[0],
                            bundle_id=str(warehouse.get("bundle_id") or ""),
                        )
                        if resolved is not None:
                            inner_supply_id = resolved
                except Exception:
                    pass
                supply = fbo_repo.create_supply(
                    {
                        "batch_id": batch.id,
                        "title": f"{title} — {cluster_name}",
                        "supply_kind": supply_kind,
                        "delivery_type": delivery_type,
                        "status": STATUS_ASSIGNED,
                        "ozon_order_id": str(order_id),
                        "ozon_supply_id": str(inner_supply_id),
                        "ozon_draft_id": str(draft_id),
                        "ozon_bundle_id": str(warehouse.get("bundle_id") or ""),
                        "ozon_cluster_id": str(macro_id),
                        "ozon_cluster_name": cluster_name or str(warehouse.get("cluster_name") or ""),
                        "ozon_warehouse_id": str(warehouse.get("storage_warehouse_id") or ""),
                        "ozon_warehouse_name": str(warehouse.get("warehouse_name") or ""),
                        "dropoff_warehouse_id": dropoff_id,
                        "dropoff_warehouse_name": dropoff_name,
                        "timeslot_from": ts_from,
                        "timeslot_to": ts_to,
                        "items": local_items,
                    },
                    manager_user_id=user.id,
                )
                row["local_supply_id"] = supply.id
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)
            results.append(row)

        ok_count = sum(1 for r in results if r.get("local_supply_id"))
        fbo_repo.update_batch(
            batch.id,
            {"status": BATCH_STATUS_SUBMITTED if ok_count else BATCH_STATUS_PLANNING},
        )
        batch_row = fbo_repo.get_batch(batch.id)
        return {
            "ok": ok_count > 0,
            "batch": batch_to_dict(batch_row, include_details=True) if batch_row else None,
            "results": results,
            "created": ok_count,
            "errors": sum(1 for r in results if r.get("error")),
        }

    @app.post("/api/warehouse/marketplaces/ozon-fbo/batches/{batch_id}/labels/generate")
    async def api_ozon_fbo_batch_labels_generate(
        batch_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        supplies = fbo_repo.list_supplies_for_batch(batch_id)
        if not supplies:
            raise HTTPException(status_code=404, detail="В пакете нет заявок")
        ozon = _ozon()
        out: list[dict[str, Any]] = []
        for supply in supplies:
            supply_dict = supply_to_dict(supply, include_details=True)
            try:
                inner_id = resolve_inner_supply_id(ozon, supply_dict)
            except ValueError as exc:
                out.append({"supply_id": supply.id, "error": str(exc)})
                continue
            try:
                payload: dict[str, Any] = {"supply_id": inner_id}
                cargo_ids = [
                    str(c.get("ozon_cargo_id") or "").strip()
                    for c in supply_dict.get("cargoes") or []
                    if str(c.get("ozon_cargo_id") or "").strip()
                ]
                if cargo_ids:
                    payload["cargo_ids"] = cargo_ids
                data = ozon.fbo_cargo_labels_create(payload)
                file_guid = _extract_first_id(data, ("file_guid", "fileGuid", "guid"))
                operation_id = _extract_first_id(data, ("operation_id", "operationId", "task_id", "taskId"))
                if file_guid:
                    fbo_repo.update_supply(
                        supply.id,
                        {
                            "labels_file_guid": file_guid,
                            "labels_operation_id": operation_id,
                            "labels_filename": f"ozon_fbo_labels_{inner_id}.pdf",
                            "status": STATUS_LABELS_READY,
                        },
                    )
                else:
                    fbo_repo.update_supply(
                        supply.id,
                        {"labels_operation_id": operation_id, "status": STATUS_READY},
                    )
                out.append({"supply_id": supply.id, "file_guid": file_guid, "operation_id": operation_id})
            except Exception as exc:  # noqa: BLE001
                out.append({"supply_id": supply.id, "error": str(exc)})
        return {"ok": True, "results": out}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/batches/{batch_id}/labels.pdf")
    async def api_ozon_fbo_batch_labels_pdf(
        batch_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        supplies = fbo_repo.list_supplies_for_batch(batch_id)
        if not supplies:
            raise HTTPException(status_code=404, detail="В пакете нет заявок")
        ozon = _ozon()
        pdfs: list[bytes] = []
        for supply in supplies:
            guid = str(supply.labels_file_guid or "").strip()
            if not guid:
                continue
            try:
                pdfs.append(ozon.fbo_cargo_labels_file(guid))
            except Exception:
                continue
        if not pdfs:
            raise HTTPException(status_code=400, detail="Нет готовых этикеток. Сначала сгенерируйте этикетки.")
        merged = merge_label_pdfs(pdfs)
        if not merged:
            raise HTTPException(status_code=500, detail="Не удалось объединить PDF (pypdf)")
        batch = fbo_repo.get_batch(batch_id)
        title = batch.title if batch else f"batch_{batch_id}"
        filename = f"ozon_fbo_labels_{batch_id}_{title[:40]}.pdf"
        return Response(
            content=merged,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/warehouse/marketplaces/ozon-fbo/batches/{batch_id}/cargoes/send-all")
    async def api_ozon_fbo_batch_send_cargoes(
        batch_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        supplies = fbo_repo.list_supplies_for_batch(batch_id)
        results: list[dict[str, Any]] = []
        for supply in supplies:
            supply_dict = supply_to_dict(supply, include_details=True)
            try:
                result = _send_supply_cargoes_to_ozon(supply.id, supply_dict)
                results.append(
                    {
                        "supply_id": supply.id,
                        "operation_id": result.get("operation_id"),
                        "inner_supply_id": result.get("inner_supply_id"),
                        "ok": True,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append({"supply_id": supply.id, "error": str(exc)})
        if any(r.get("ok") for r in results):
            fbo_repo.update_batch(batch_id, {"status": BATCH_STATUS_PACKING})
        return {"ok": True, "results": results}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/ozon/supply-orders")
    async def api_ozon_fbo_supply_orders(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        scope = str(request.query_params.get("scope") or "active").strip().lower()
        try:
            overview = fetch_supply_orders_overview(_ozon(), scope=scope)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        local_rows = fbo_repo.list_supplies({})
        local_by_order: dict[str, list[int]] = {}
        local_by_inner: dict[str, int] = {}
        for r in local_rows:
            oid = str(r.ozon_order_id or "").strip()
            sid = str(r.ozon_supply_id or "").strip()
            if oid:
                local_by_order.setdefault(oid, []).append(r.id)
            if sid:
                local_by_inner[sid] = r.id
        for order in overview.get("orders") or []:
            oid = str(order.get("order_id") or "")
            lines = order.get("supply_lines") or []
            inner_ids = [str(ln.get("supply_id")) for ln in lines if ln.get("supply_id")]
            local_ids = list(local_by_order.get(oid, []))
            for inner in inner_ids:
                if inner in local_by_inner:
                    lid = local_by_inner[inner]
                    if lid not in local_ids:
                        local_ids.append(lid)
            order["local_supply_ids"] = local_ids
            order["local_supply_id"] = local_ids[0] if local_ids else None
            order["in_local_system"] = bool(local_ids)
        return {"ok": True, **overview}

    @app.get("/api/warehouse/marketplaces/ozon-fbo/ozon/supply-orders/{order_id}")
    async def api_ozon_fbo_supply_order_detail(
        order_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        try:
            orders = get_supply_orders(_ozon(), [int(order_id)])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not orders:
            raise HTTPException(status_code=404, detail="Заявка не найдена в Ozon")
        order = normalize_supply_order(orders[0])
        items: list[dict[str, Any]] = []
        lines = order.get("supply_lines") or []
        if len(lines) > 1:
            for ln in lines:
                bundle_id = str(ln.get("bundle_id") or "")
                if bundle_id:
                    try:
                        for bi in get_bundle_items(_ozon(), bundle_id):
                            items.append(
                                {
                                    "offer_id": str(bi.get("offer_id") or bi.get("sku") or ""),
                                    "name": str(bi.get("name") or ""),
                                    "quantity": int(bi.get("quantity") or 0),
                                    "warehouse_name": ln.get("warehouse_name"),
                                    "supply_id": ln.get("supply_id"),
                                }
                            )
                    except Exception:
                        pass
        elif order.get("bundle_id"):
            try:
                items = get_bundle_items(_ozon(), str(order["bundle_id"]))
            except Exception:
                items = []
        local = fbo_repo.list_supplies({})
        local_ids: list[int] = []
        for row in local:
            if str(row.ozon_order_id) == str(order_id):
                local_ids.append(row.id)
            elif str(row.ozon_supply_id) == str(order_id):
                local_ids.append(row.id)
            else:
                for ln in lines:
                    if ln.get("supply_id") and str(row.ozon_supply_id) == str(ln.get("supply_id")):
                        local_ids.append(row.id)
        local_id = local_ids[0] if local_ids else None
        return {
            "ok": True,
            "order": order,
            "items": items,
            "local_supply_id": local_id,
            "local_supply_ids": local_ids,
            "raw": orders[0],
        }

    @app.post("/api/warehouse/marketplaces/ozon-fbo/batches/import-ozon")
    async def api_ozon_fbo_import_ozon_batch(
        body: dict,
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        """Привязать существующие заявки Ozon к локальному пакету (без повторного создания в Ozon)."""
        raw_ids = body.get("order_ids") or []
        order_ids = [int(x) for x in raw_ids if str(x).strip()]
        if not order_ids:
            raise HTTPException(status_code=400, detail="Укажите order_ids")
        ozon = _ozon()
        try:
            orders = get_supply_orders(ozon, order_ids)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not orders:
            raise HTTPException(status_code=404, detail="Заявки не найдены в Ozon")
        title = str(body.get("title") or "").strip() or f"Импорт из Ozon ({len(orders)} заявок)"
        first = normalize_supply_order(orders[0])
        batch = fbo_repo.create_batch(
            {
                "title": title,
                "delivery_type": first.get("delivery_type") or "direct",
                "dropoff_warehouse_id": str((orders[0].get("drop_off_warehouse") or {}).get("warehouse_id") or ""),
                "dropoff_warehouse_name": str((orders[0].get("drop_off_warehouse") or {}).get("name") or ""),
                "timeslot_from": str(first.get("timeslot_from") or ""),
                "timeslot_to": str(first.get("timeslot_to") or ""),
                "status": BATCH_STATUS_SUBMITTED,
            },
            manager_user_id=user.id,
        )
        imported: list[dict[str, Any]] = []
        all_local = fbo_repo.list_supplies({})
        for raw in orders:
            for norm in expand_order_supplies(raw):
                order_id = str(norm.get("order_id") or "")
                inner_id = str(norm.get("supply_id") or "")
                bundle_id = str(norm.get("bundle_id") or "")
                dedupe_key = inner_id or f"{order_id}:{bundle_id}"
                existing = [
                    r
                    for r in all_local
                    if (inner_id and str(r.ozon_supply_id) == inner_id)
                    or (not inner_id and str(r.ozon_supply_id) == order_id and str(r.ozon_bundle_id) == bundle_id)
                ]
                if existing:
                    imported.append(
                        {
                            "order_id": order_id,
                            "supply_id": inner_id,
                            "local_supply_id": existing[0].id,
                            "skipped": True,
                        }
                    )
                    continue
                items: list[dict[str, Any]] = []
                if bundle_id:
                    try:
                        for bi in get_bundle_items(ozon, bundle_id):
                            items.append(
                                {
                                    "sku": str(bi.get("offer_id") or bi.get("sku") or ""),
                                    "name": str(bi.get("name") or ""),
                                    "quantity": int(bi.get("quantity") or 0),
                                }
                            )
                    except Exception:
                        pass
                cluster_label = str(norm.get("warehouse_name") or norm.get("macrolocal_cluster_id") or "")
                supply = fbo_repo.create_supply(
                    {
                        "batch_id": batch.id,
                        "title": f"Ozon #{norm.get('order_number') or order_id}"
                        + (f" — {cluster_label}" if cluster_label else ""),
                        "supply_kind": str(body.get("supply_kind") or "pallet"),
                        "delivery_type": norm.get("delivery_type") or "direct",
                        "status": STATUS_ASSIGNED,
                        "ozon_order_id": order_id,
                        "ozon_supply_id": inner_id or order_id,
                        "ozon_bundle_id": bundle_id,
                        "ozon_cluster_id": str(norm.get("macrolocal_cluster_id") or ""),
                        "ozon_warehouse_id": str(norm.get("warehouse_id") or ""),
                        "ozon_warehouse_name": str(norm.get("warehouse_name") or ""),
                        "timeslot_from": str(norm.get("timeslot_from") or ""),
                        "timeslot_to": str(norm.get("timeslot_to") or ""),
                        "items": items,
                    },
                    manager_user_id=user.id,
                )
                all_local.append(supply)
                imported.append(
                    {
                        "order_id": order_id,
                        "supply_id": inner_id,
                        "local_supply_id": supply.id,
                        "skipped": False,
                    }
                )
        batch_row = fbo_repo.get_batch(batch.id)
        return {
            "ok": True,
            "batch": batch_to_dict(batch_row, include_details=True) if batch_row else None,
            "imported": imported,
        }
