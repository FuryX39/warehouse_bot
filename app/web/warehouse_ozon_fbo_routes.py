"""HTTP API FBO-поставок Ozon для вкладки «Маркетплейсы»."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from app.adapters.ozon import OzonAdapter
from app.ozon_fbo_supply_repository import (
    STATUS_LABELS_READY,
    STATUS_READY,
    STATUS_SENT_TO_OZON,
    OzonFboSupplyRepository,
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
            "statuses": [
                {"id": "draft", "name": "Черновик"},
                {"id": "assigned", "name": "Назначена"},
                {"id": "packing", "name": "Сборка"},
                {"id": "ready", "name": "Готова к Ozon"},
                {"id": "sent_to_ozon", "name": "Отправлена в Ozon"},
                {"id": "labels_ready", "name": "Этикетки готовы"},
                {"id": "done", "name": "Завершена"},
            ],
        }

    def _ozon() -> OzonAdapter:
        if ozon_adapter is None or not ozon_adapter.is_configured():
            raise HTTPException(
                status_code=400,
                detail="Ozon API не настроен: задайте OZON_CLIENT_ID и OZON_API_KEY",
            )
        return ozon_adapter

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

    def _supply_payload(supply: dict, body: dict) -> dict:
        draft_id = str(body.get("draft_id") or supply.get("ozon_draft_id") or "").strip()
        timeslot_id = str(body.get("timeslot_id") or "").strip()
        if not draft_id:
            raise ValueError("У заявки нет ID черновика Ozon")
        if not timeslot_id:
            raise ValueError("Укажите timeslot_id")
        return {"draft_id": draft_id, "timeslot_id": timeslot_id}

    def _cargoes_payload(supply: dict) -> dict:
        supply_id = str(supply.get("ozon_supply_id") or "").strip()
        if not supply_id:
            raise ValueError("У заявки нет ID поставки Ozon")
        cargoes = []
        for cargo in supply.get("cargoes") or []:
            items = []
            for item in cargo.get("items") or []:
                sku = str(item.get("sku") or "").strip()
                qty = int(item.get("quantity") or 0)
                if not sku or qty <= 0:
                    continue
                row = {"sku": sku, "quantity": qty}
                exp = str(item.get("expiration_date") or "").strip()
                if exp:
                    row["expiration_date"] = exp
                items.append(row)
            if not items:
                continue
            cargoes.append(
                {
                    "cargo_number": str(cargo.get("cargo_number") or "").strip(),
                    "items": items,
                }
            )
        if not cargoes:
            raise ValueError("Добавьте грузоместа и состав")
        return {
            "supply_id": supply_id,
            "delete_current_version": True,
            "cargoes": cargoes,
        }

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
            payload = _cargoes_payload(supply)
            data = _ozon().fbo_cargoes_create(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        operation_id = _extract_first_id(data, ("operation_id", "operationId", "task_id", "taskId"))
        fbo_repo.update_supply(
            supply_id,
            {"status": STATUS_SENT_TO_OZON, "cargoes_operation_id": operation_id},
        )
        return {"ok": True, "payload": payload, "data": data, "operation_id": operation_id}

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
        ozon_supply_id = str(supply.get("ozon_supply_id") or "").strip()
        if not ozon_supply_id:
            raise HTTPException(status_code=400, detail="У заявки нет ID поставки Ozon")
        payload = {"supply_ids": [ozon_supply_id]}
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
        ozon_supply_id = str(supply.get("ozon_supply_id") or "").strip()
        if not ozon_supply_id:
            raise HTTPException(status_code=400, detail="У заявки нет ID поставки Ozon")
        cargo_ids = [
            str(c.get("ozon_cargo_id") or "").strip()
            for c in supply.get("cargoes") or []
            if str(c.get("ozon_cargo_id") or "").strip()
        ]
        payload: dict[str, Any] = {"supply_id": ozon_supply_id}
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
                    "labels_filename": f"ozon_fbo_cargo_labels_{ozon_supply_id}.pdf",
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
