"""HTTP API CRM для новой панели /warehouse."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.crm_repository import CrmRepository
from app.warehouse_users_repository import WarehouseUserRow


def register_warehouse_crm_routes(
    app,
    crm_repo: CrmRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/crm/meta")
    async def api_crm_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        return crm_repo.get_meta()

    @app.put("/api/warehouse/crm/statuses")
    async def api_crm_save_statuses(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"statuses": crm_repo.save_statuses(items)}

    @app.put("/api/warehouse/crm/groups")
    async def api_crm_save_groups(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"groups": crm_repo.save_groups(items)}

    @app.put("/api/warehouse/crm/types")
    async def api_crm_save_types(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"types": crm_repo.save_types(items)}

    @app.put("/api/warehouse/crm/price-types")
    async def api_crm_save_price_types(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items должен быть массивом")
        return {"price_types": crm_repo.save_price_types(items)}

    @app.get("/api/warehouse/crm/counterparties")
    async def api_crm_list_counterparties(
        request: Request,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        filters = _filters_from_query(request.query_params)
        rows = crm_repo.list_counterparties(filters)
        return {
            "counterparties": [
                crm_repo.counterparty_to_dict(r, include_contacts=False) for r in rows
            ]
        }

    @app.get("/api/warehouse/crm/counterparties/{counterparty_id}")
    async def api_crm_get_counterparty(
        counterparty_id: int,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = crm_repo.get_counterparty(counterparty_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Контрагент не найден")
        return {"counterparty": crm_repo.counterparty_to_dict(row)}

    @app.post("/api/warehouse/crm/counterparties")
    async def api_crm_create_counterparty(
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = crm_repo.create_counterparty(body)
        return {"counterparty": crm_repo.counterparty_to_dict(row)}

    @app.put("/api/warehouse/crm/counterparties/{counterparty_id}")
    async def api_crm_update_counterparty(
        counterparty_id: int,
        body: dict,
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        row = crm_repo.update_counterparty(counterparty_id, body)
        if row is None:
            raise HTTPException(status_code=404, detail="Контрагент не найден")
        return {"counterparty": crm_repo.counterparty_to_dict(row)}


def _filters_from_query(params: Any) -> dict[str, str]:
    keys = (
        "q",
        "status_id",
        "group_id",
        "type_id",
        "price_type_id",
        "phone",
        "email",
        "inn",
        "full_name",
        "legal_address",
        "address_comment",
        "fias_code",
        "kpp",
        "ogrn",
        "okpo",
        "discount_card_number",
        "contact",
    )
    out: dict[str, str] = {}
    for key in keys:
        raw = params.get(key)
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
    return out
