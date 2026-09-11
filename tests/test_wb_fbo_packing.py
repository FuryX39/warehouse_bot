"""Задания FBO-упаковки Wildberries: QR поставки, листы паллет, короба."""

from __future__ import annotations

import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.adapters.wildberries import WildberriesAdapter, _parse_fbw_supply_id
from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.crm_repository import CrmRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_fbo_packing_repository import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DONE,
    JOB_STATUS_IN_PROGRESS,
    LINE_DONE,
    LINE_PENDING,
    WbFboPackingRepository,
)
from app.wb_fbo_packing_service import create_wb_fbo_packing_job, preview_wb_fbo_supply
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.web.warehouse_wb_fbo_routes import register_warehouse_wb_fbo_routes


def _pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 80))
    c.setFont("Helvetica", 10)
    c.drawString(10, 40, text)
    c.save()
    return buf.getvalue()


class FakeFbwAdapter(WildberriesAdapter):
    def __init__(self) -> None:
        super().__init__(api_token="test-token")
        self.supply = {
            "supplyDate": "2026-09-22T00:00:00Z",
            "warehouseName": "СЦ Новосибирск 4",
            "sellerName": 'ООО "ШАЙН СИСТЕМС"',
            "boxTypeID": 1,
        }
        self.goods = [{"barcode": "4673746970515", "vendorCode": "SS743"}]
        self.packages = [
            {
                "packageCode": "$Ts;N+q;R6a9;1;Pyu678c;TAS",
                "quantity": 10,
                "barcodes": [{"barcode": "4673746970515", "quantity": 10}],
            },
            {
                "packageCode": "$Ts;FCw;dfwy;1;Pyt2sDs;TAS",
                "quantity": 8,
                "barcodes": [{"barcode": "4673746970515", "quantity": 8}],
            },
        ]

    def is_configured(self) -> bool:
        return True

    def fetch_fbw_supply(self, supply_id: str | int) -> dict:
        assert str(supply_id) == "41357389"
        return dict(self.supply)

    def fetch_fbw_supply_goods(self, supply_id: str | int) -> list[dict]:
        assert str(supply_id) == "41357389"
        return list(self.goods)

    def fetch_fbw_supply_packages(self, supply_id: str | int) -> list[dict]:
        assert str(supply_id) == "41357389"
        return list(self.packages)


def _catalog(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    repo.create_product(
        {
            "name": "Товар FBW",
            "sku": "SS743",
            "code": "00011",
            "is_kit": False,
            "barcodes": [{"barcode": "4673746970515", "label": "", "group": ""}],
            "components": [],
        }
    )
    return repo


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


def test_parse_fbw_supply_id_rejects_gi() -> None:
    try:
        _parse_fbw_supply_id("WB-GI-277689956")
    except ValueError as exc:
        assert "числовой ID" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_preview_and_create_job(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "wb_fbo")
    packing.init_schema()
    adapter = FakeFbwAdapter()

    preview = preview_wb_fbo_supply(adapter, "41357389")
    assert preview["city"] == "Новосибирск"
    assert preview["box_count"] == 2
    assert preview["skus"][0]["sku"] == "SS743"

    qr_pdf = _pdf("WB-GI-27768 9956")
    job = create_wb_fbo_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        supply_id="41357389",
        pallet_count=4,
        city="",
        packer_user_ids=[7],
        created_by_user_id=1,
        supply_qr_pdf=qr_pdf,
    )
    assert job.supply_id == "41357389"
    assert job.city == "Новосибирск"
    assert job.pallet_count == 4
    assert job.supply_qr_code == "WB-GI-277689956"
    assert job.line_total == 2
    assert job.lines[0].sku == "SS743"
    assert packing.read_job_pdf(job.id, "supply-qr").startswith(b"%PDF")
    sheets = packing.read_job_pdf(job.id, "pallet-sheets")
    assert sheets.startswith(b"%PDF")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(sheets))
    assert len(reader.pages) == 4
    text = reader.pages[0].extract_text() or ""
    assert "Палет 1 из 4" in text
    assert "Новосибирск" in text
    assert packing.read_line_pdf(job.id, job.lines[0].id).startswith(b"%PDF")


def test_preview_create_and_scan_routes(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "wb_fbo_http")
    packing.init_schema()
    packer = _packer()
    adapter = FakeFbwAdapter()
    app = FastAPI()
    register_warehouse_wb_fbo_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: []),
        SimpleNamespace(adapters=[adapter]),
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/fbo-packing",),
    )
    client = TestClient(app)

    preview = client.get(
        "/api/warehouse/marketplaces/wb-fbo/preview",
        params={"supply_id": "41357389"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["box_count"] == 2
    assert preview.json()["city"] == "Новосибирск"

    created = client.post(
        "/api/warehouse/marketplaces/wb-fbo/jobs",
        data={
            "supply_id": "41357389",
            "pallet_count": "2",
            "city": "",
            "packer_user_ids": "[7]",
        },
        files={"qr": ("qr.pdf", _pdf("WB-GI-277689956"), "application/pdf")},
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["line_total"] == 2
    assert job["pallet_count"] == 2
    assert job["supply_qr_code"] == "WB-GI-277689956"
    job_id = job["id"]

    sheets = client.get(f"/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/pallet-sheets.pdf")
    assert sheets.status_code == 200
    assert sheets.content.startswith(b"%PDF")

    mine = client.get("/api/v1/fbo-packing/my")
    assert mine.status_code == 200
    assert mine.json()["jobs"][0]["id"] == job_id

    scanned = client.post(
        f"/api/v1/fbo-packing/jobs/{job_id}/scan-product",
        json={"barcode": "4673746970515", "batch": False, "auto_close": True},
    )
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["line"]["sku"] == "SS743"
    assert scanned.json()["pdf_base64"]
    assert scanned.json()["job"]["line_done"] == 1

    batch = client.post(
        f"/api/v1/fbo-packing/jobs/{job_id}/scan-product",
        json={"barcode": "SS743", "batch": True, "auto_close": True},
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["job"]["status"] == "done"


def test_set_line_status_pending_to_done_and_back(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "wb_fbo_status")
    packing.init_schema()
    packer = _packer()
    adapter = FakeFbwAdapter()
    app = FastAPI()
    register_warehouse_wb_fbo_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: []),
        SimpleNamespace(adapters=[adapter]),
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/fbo-packing",),
    )
    client = TestClient(app)
    created = client.post(
        "/api/warehouse/marketplaces/wb-fbo/jobs",
        data={
            "supply_id": "41357389",
            "pallet_count": "1",
            "city": "",
            "packer_user_ids": "[7]",
        },
        files={"qr": ("qr.pdf", _pdf("WB-GI-277689956"), "application/pdf")},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]
    line_id = created.json()["job"]["lines"][0]["id"]
    prefix = f"/api/v1/fbo-packing/jobs/{job_id}/lines/{line_id}"

    done = client.post(f"{prefix}/set-status", json={"status": "done"})
    assert done.status_code == 200, done.text
    assert done.json()["line"]["status"] == LINE_DONE

    pending = client.post(f"{prefix}/set-status", json={"status": "pending"})
    assert pending.status_code == 200, pending.text
    assert pending.json()["line"]["status"] == LINE_PENDING
    assert packing.get_job(job_id).status == JOB_STATUS_IN_PROGRESS


def test_set_line_status_reopens_done_job(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "wb_fbo_reopen")
    packing.init_schema()
    packer = _packer()
    adapter = FakeFbwAdapter()
    app = FastAPI()
    register_warehouse_wb_fbo_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: []),
        SimpleNamespace(adapters=[adapter]),
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/fbo-packing",),
    )
    client = TestClient(app)
    created = client.post(
        "/api/warehouse/marketplaces/wb-fbo/jobs",
        data={
            "supply_id": "41357389",
            "pallet_count": "1",
            "city": "",
            "packer_user_ids": "[7]",
        },
        files={"qr": ("qr.pdf", _pdf("WB-GI-277689956"), "application/pdf")},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]
    lines = created.json()["job"]["lines"]
    for line in lines:
        resp = client.post(
            f"/api/v1/fbo-packing/jobs/{job_id}/lines/{line['id']}/set-status",
            json={"status": "done"},
        )
        assert resp.status_code == 200, resp.text
    assert packing.get_job(job_id).status == JOB_STATUS_DONE

    reopened = client.post(
        f"/api/v1/fbo-packing/jobs/{job_id}/lines/{lines[0]['id']}/set-status",
        json={"status": "pending"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["line"]["status"] == LINE_PENDING
    assert packing.get_job(job_id).status == JOB_STATUS_IN_PROGRESS


def test_manager_can_cancel_done_fbo_job(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "wb_fbo_cancel")
    packing.init_schema()
    packer = _packer()
    adapter = FakeFbwAdapter()
    app = FastAPI()
    register_warehouse_wb_fbo_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: []),
        SimpleNamespace(adapters=[adapter]),
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/fbo-packing",),
    )
    client = TestClient(app)
    created = client.post(
        "/api/warehouse/marketplaces/wb-fbo/jobs",
        data={
            "supply_id": "41357389",
            "pallet_count": "1",
            "city": "",
            "packer_user_ids": "[7]",
        },
        files={"qr": ("qr.pdf", _pdf("WB-GI-277689956"), "application/pdf")},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]
    for line in created.json()["job"]["lines"]:
        resp = client.post(
            f"/api/v1/fbo-packing/jobs/{job_id}/lines/{line['id']}/set-status",
            json={"status": "done"},
        )
        assert resp.status_code == 200, resp.text
    assert packing.get_job(job_id).status == JOB_STATUS_DONE

    cancelled = client.post(f"/api/warehouse/marketplaces/wb-fbo/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["job"]["status"] == JOB_STATUS_CANCELLED
    assert packing.get_job(job_id).status == JOB_STATUS_CANCELLED
    mine = client.get("/api/v1/fbo-packing/my")
    assert mine.status_code == 200
    assert all(item["id"] != job_id for item in mine.json()["jobs"])
