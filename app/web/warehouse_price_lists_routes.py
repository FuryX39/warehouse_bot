"""HTTP API формирования прайс-листов."""

from __future__ import annotations

import asyncio
from datetime import date
import json
import logging

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.vseinstrumenti_pricat import (
    build_vseinstrumenti_pricat,
    build_vseinstrumenti_quantity_template,
)
from app.warehouse_users_repository import WarehouseUserRow

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 20 * 1024 * 1024


async def _xlsx(file: UploadFile, title: str) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail=f"{title}: нужен файл .xlsx")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{title}: файл пустой")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"{title}: файл больше 20 МБ")
    return data


def _date(value: str, title: str) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{title}: укажите корректную дату") from exc


def register_warehouse_price_lists_routes(
    app,
    catalog_repo: CatalogRepository,
    crm_repo: CrmRepository,
    require_warehouse_user,
) -> None:
    @app.get("/api/warehouse/products/price-lists/vseinstrumenti/meta")
    async def api_vseinstrumenti_pricat_meta(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        rows = await asyncio.to_thread(crm_repo.list_counterparties, {})
        return {
            "counterparties": [
                {
                    "id": row.id,
                    "name": row.full_name or f"Контрагент #{row.id}",
                    "inn": row.inn,
                    "kpp": row.kpp,
                    "gln": row.gln,
                }
                for row in sorted(rows, key=lambda item: (item.full_name or "").casefold())
            ]
        }

    @app.get("/api/warehouse/products/price-lists/vseinstrumenti/quantity-template")
    async def api_vseinstrumenti_quantity_template(
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        return Response(
            content=build_vseinstrumenti_quantity_template(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="PRICAT_quantity_template.xlsx"'
            },
        )

    @app.post("/api/warehouse/products/price-lists/vseinstrumenti/pricat")
    async def api_vseinstrumenti_pricat(
        buyer_id: int = Form(...),
        supplier_id: int = Form(...),
        document_name: str = Form(...),
        document_date: str = Form(...),
        contract_number: str = Form(...),
        contract_date: str = Form(...),
        price_list_type: str = Form("Основной"),
        valid_from: str = Form(...),
        valid_to: str = Form(...),
        internal_comment: str = Form(""),
        buyer_message: str = Form(""),
        quantity_file: UploadFile = File(...),
        _: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> Response:
        quantity_data = await _xlsx(quantity_file, "Файл количества")
        try:
            buyer, supplier, catalog_data = await asyncio.gather(
                asyncio.to_thread(crm_repo.get_counterparty, buyer_id),
                asyncio.to_thread(crm_repo.get_counterparty, supplier_id),
                asyncio.to_thread(catalog_repo.list_products_for_export, {}),
            )
            if buyer is None:
                raise ValueError("Покупатель не найден")
            if supplier is None:
                raise ValueError("Поставщик не найден")
            doc_name = str(document_name or "").strip()
            agreement = str(contract_number or "").strip()
            if not doc_name:
                raise ValueError("Укажите название или номер документа")
            if not agreement:
                raise ValueError("Укажите номер договора")
            kind = str(price_list_type or "").strip()
            if kind not in {"Основной", "Акционный"}:
                raise ValueError("Тип прайса должен быть «Основной» или «Акционный»")
            start = _date(valid_from, "Начало действия цен")
            finish = _date(valid_to, "Окончание действия цен")
            if finish < start:
                raise ValueError("Окончание действия цен раньше начала")
            result, stats = await asyncio.to_thread(
                build_vseinstrumenti_pricat,
                quantity_data,
                catalog_products=catalog_data.get("products") or [],
                buyer=crm_repo.counterparty_to_dict(buyer, include_contacts=False),
                supplier=crm_repo.counterparty_to_dict(supplier, include_contacts=False),
                header={
                    "document_name": doc_name,
                    "document_date": _date(document_date, "Дата документа"),
                    "contract_number": agreement,
                    "contract_date": _date(contract_date, "Дата договора"),
                    "price_list_type": kind,
                    "valid_from": start,
                    "valid_to": finish,
                    "internal_comment": internal_comment,
                    "buyer_message": buyer_message,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("vseinstrumenti PRICAT generation failed")
            raise HTTPException(status_code=500, detail="Ошибка формирования PRICAT") from exc
        return Response(
            content=result,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="PRICAT_result.xlsx"',
                "X-PRICAT-Stats": json.dumps(stats, ensure_ascii=True, separators=(",", ":")),
            },
        )
