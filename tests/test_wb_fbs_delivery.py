"""Передача поставок WB FBS в доставку."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.wildberries import _parse_shipping_points
from app.config import Settings
from app.fbs_packing_repository import FbsPackingRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_fbs_delivery import (
    SHIPPING_SELF,
    SHIPPING_TRANSPORT,
    deliver_job_supplies,
    job_wb_supply_ids,
    load_job_delivery_meta,
    match_default_shipping_points,
    normalize_waybill_uuid,
)
from app.web.warehouse_fbs_packing_routes import register_warehouse_fbs_packing_routes
from app.web.warehouse_tasks_api_auth import TasksApiActor
from tests.test_wb_fbs_packing_jobs import FakeWbAdapter, _catalog, _png_bytes


class FakeDeliverAdapter(FakeWbAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.shipping_calls: list[dict] = []
        self.waybill_calls: list[dict] = []
        self.delivered: list[str] = []
        self.points = [
            {
                "id": 33070,
                "city": "Обь",
                "address": "3307 км, д. 30, к. 2",
                "name": "СЦ Обь",
                "office_type": "sc",
            },
            {
                "id": 12,
                "city": "Новосибирск",
                "address": "улица Докучаева, 1/2",
                "name": "СЦ Докучаева",
                "office_type": "sc",
            },
        ]

    def fetch_supply(self, supply_id: str) -> dict:
        return {"id": supply_id, "done": False, "cargoType": 1}

    def list_fbs_shipping_points(self, *, city: str, cargo_type: int = 1) -> list[dict]:
        return [dict(item) for item in self.points if item["city"] == city]

    def list_supply_shipping_points(
        self,
        supply_id: str,
        *,
        city: str,
        office_type: str = "sc,sw,pp,pfc,gpp",
        shipping_dt: str | None = None,
    ) -> list[dict]:
        return self.list_fbs_shipping_points(city=city, cargo_type=1)

    def set_supplies_shipping_method(self, items: list[dict]) -> dict:
        self.shipping_calls.extend(items)
        return {"results": [{"supplyId": item["supplyId"], "success": True} for item in items]}

    def set_supplies_waybill(self, items: list[dict]) -> dict:
        self.waybill_calls.extend(items)
        return {"results": [{"supplyId": item["supplyId"], "success": True} for item in items]}

    def deliver_supply(self, supply_id: str) -> None:
        self.delivered.append(str(supply_id))

    def fetch_supply_barcode(self, supply_id: str, *, sticker_type: str = "png") -> dict[str, str]:
        return {
            "barcode": str(supply_id),
            "png_b64": base64.b64encode(_png_bytes()).decode("ascii"),
        }


def _packer() -> WarehouseUserRow:
    return WarehouseUserRow(
        id=7,
        login="packer",
        display_name="packer",
        group_id=None,
        group_name="",
        telegram_nick="",
        is_admin=False,
        is_active=True,
        permissions={},
        created_at_ts=0,
        updated_at_ts=0,
    )


def _client(db_url: str, tmp_path, adapter: FakeDeliverAdapter) -> TestClient:
    catalog, db_url = _catalog(db_url)
    catalog.create_product(
        {
            "name": "WB товар",
            "sku": "SKU-WB",
            "code": "00011",
            "is_kit": False,
            "barcodes": [{"barcode": "WB-SKU", "label": "", "group": ""}],
            "components": [],
        }
    )
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_deliver")
    packing.init_schema()
    packer = _packer()
    app = FastAPI()
    register_warehouse_fbs_packing_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: []),
        Settings(telegram_bot_token="t", db_url=db_url, movement_db_url=db_url),
        SimpleNamespace(adapters=[adapter]),
        lambda: packer,
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
    )
    return TestClient(app)


def test_job_wb_supply_ids_from_merged_title() -> None:
    job = SimpleNamespace(
        supply_id="WB-GI-A",
        sheet_title="Объединено: WB-GI-A + WB-GI-B",
    )
    assert job_wb_supply_ids(job) == ["WB-GI-A", "WB-GI-B"]


def test_match_default_novosibirsk_points() -> None:
    points = match_default_shipping_points(
        [
            {"id": 1, "city": "Обь", "address": "3307 км, д. 30, к. 2", "name": "СЦ"},
            {"id": 2, "city": "Новосибирск", "address": "ул. Докучаева, 1/2", "name": "СЦ"},
        ]
    )
    assert points[0]["key"] == "oby_3307"
    assert points[0]["shipping_point_id"] == 1
    assert points[0]["available"] is True
    assert points[1]["key"] == "nsk_dokuchaeva"
    assert points[1]["shipping_point_id"] == 2


def test_parse_shipping_points_payload() -> None:
    parsed = _parse_shipping_points(
        {
            "shippingPoints": [
                {
                    "id": 12,
                    "city": "Новосибирск",
                    "address": "улица Докучаева, 1/2",
                    "officeType": "sc",
                }
            ]
        }
    )
    assert parsed == [
        {
            "id": 12,
            "city": "Новосибирск",
            "address": "улица Докучаева, 1/2",
            "name": "",
            "office_type": "sc",
            "fulfillment": False,
        }
    ]


def test_normalize_waybill_uuid() -> None:
    assert (
        normalize_waybill_uuid("550E8400E29B41D4A716446655440000")
        == "550e8400-e29b-41d4-a716-446655440000"
    )


def test_deliver_self_shipping_builds_qr_pdf(db_url: str, tmp_path) -> None:
    catalog, db_url = _catalog(db_url)
    catalog.create_product(
        {
            "name": "WB товар",
            "sku": "SKU-WB",
            "code": "00011",
            "is_kit": False,
            "barcodes": [{"barcode": "WB-SKU", "label": "", "group": ""}],
            "components": [],
        }
    )
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_deliver_svc")
    packing.init_schema()
    adapter = FakeDeliverAdapter()
    from app.fbs_packing_service import create_wb_packing_job

    job = create_wb_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        order_substatus="READY_TO_SHIP",
        item_limit=None,
        packer_user_ids=[7],
        created_by_user_id=1,
        supply_ids=["WB-GI-TEST"],
    )
    pdf, delivered = deliver_job_supplies(
        adapter,
        job,
        shipping_point_id=33070,
        shipping_dt="2026-10-28",
        shipping_type=SHIPPING_SELF,
    )
    assert delivered == ["WB-GI-TEST"]
    assert pdf.startswith(b"%PDF")
    assert adapter.delivered == ["WB-GI-TEST"]
    assert adapter.shipping_calls[0]["shippingType"] == SHIPPING_SELF
    assert adapter.waybill_calls == []


def test_deliver_transport_requires_waybill() -> None:
    adapter = FakeDeliverAdapter()
    job = SimpleNamespace(
        id=1,
        marketplace="wildberries",
        status="open",
        supply_id="WB-GI-TEST",
        sheet_title="",
    )
    try:
        deliver_job_supplies(
            adapter,
            job,
            shipping_point_id=33070,
            shipping_dt="2026-10-28",
            shipping_type=SHIPPING_TRANSPORT,
            waybill_uuid="",
        )
    except ValueError as exc:
        assert "УИД ТРН" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_wb_delivery_routes(db_url: str, tmp_path) -> None:
    adapter = FakeDeliverAdapter()
    client = _client(db_url, tmp_path, adapter)
    created = client.post(
        "/api/warehouse/fbs-packing/jobs",
        json={
            "marketplace": "wildberries",
            "order_substatus": "READY_TO_SHIP",
            "supply_ids": ["WB-GI-A", "WB-GI-B"],
            "packer_user_ids": [7],
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]

    meta = client.get(f"/api/warehouse/fbs-packing/jobs/{job_id}/wb-delivery")
    assert meta.status_code == 200, meta.text
    payload = meta.json()
    assert payload["supply_ids"] == ["WB-GI-A", "WB-GI-B"]
    assert payload["points"][0]["title"] == "Обь, 3307 км, д. 30, к. 2"
    assert payload["points"][0]["shipping_point_id"] == 33070
    assert payload["points"][1]["title"] == "Новосибирск, улица Докучаева, 1/2"
    assert payload["points"][1]["shipping_point_id"] == 12

    missing_trn = client.post(
        f"/api/warehouse/fbs-packing/jobs/{job_id}/wb-delivery",
        json={
            "shipping_point_id": 12,
            "shipping_dt": "2026-10-28",
            "shipping_type": "transportCompany",
        },
    )
    assert missing_trn.status_code == 400
    assert "УИД ТРН" in missing_trn.json()["detail"]

    delivered = client.post(
        f"/api/warehouse/fbs-packing/jobs/{job_id}/wb-delivery",
        json={
            "shipping_point_id": 33070,
            "point_key": "oby_3307",
            "shipping_dt": "2026-10-28",
            "shipping_type": "transportCompany",
            "waybill_uuid": "550e8400-e29b-41d4-a716-446655440000",
        },
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.headers["content-type"].startswith("application/pdf")
    assert delivered.content.startswith(b"%PDF")
    assert adapter.delivered == ["WB-GI-A", "WB-GI-B"]
    assert adapter.waybill_calls[0]["waybillUuid"] == "550e8400-e29b-41d4-a716-446655440000"
    assert {item["shippingPointId"] for item in adapter.shipping_calls} == {33070}


def test_load_delivery_meta_without_points() -> None:
    adapter = FakeDeliverAdapter()
    adapter.points = []
    job = SimpleNamespace(
        id=9,
        marketplace="wildberries",
        status="open",
        supply_id="WB-GI-TEST",
        sheet_title="",
    )
    meta = load_job_delivery_meta(adapter, job)
    assert meta["supply_ids"] == ["WB-GI-TEST"]
    assert meta["points"][0]["available"] is False
    assert meta["points"][1]["available"] is False
