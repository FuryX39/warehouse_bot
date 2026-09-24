"""Задания FBS-упаковки Wildberries."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.adapters.wildberries import WildberriesAdapter
from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.crm_repository import CrmRepository
from app.fbs_packing_repository import FbsPackingRepository
from app.fbs_packing_service import create_wb_packing_job
from app.wb_fbs_labels import (
    merged_wb_supply_title,
    normalize_wb_supply_ids,
    png_bytes_to_label_pdf,
)
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_fbs_packing_routes import register_warehouse_fbs_packing_routes
from app.web.warehouse_tasks_api_auth import TasksApiActor


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (580, 400), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    c.drawString(20, 100, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_normalize_wb_supply_ids() -> None:
    assert normalize_wb_supply_ids("WB-GI-A", ["WB-GI-B", "WB-GI-A"]) == ["WB-GI-A", "WB-GI-B"]
    assert normalize_wb_supply_ids("WB-GI-A, WB-GI-B") == ["WB-GI-A", "WB-GI-B"]
    assert merged_wb_supply_title(["WB-GI-A"]) == ""
    assert merged_wb_supply_title(["WB-GI-A", "WB-GI-B"]) == "Объединено: WB-GI-A + WB-GI-B"


def test_png_bytes_to_label_pdf() -> None:
    pdf = png_bytes_to_label_pdf(_png_bytes())
    assert pdf.startswith(b"%PDF")


class FakeWbAdapter(WildberriesAdapter):
    def __init__(self) -> None:
        super().__init__(api_token="test-token")
        self.orders = [
            {"id": 9001, "supplierArticle": "SKU-WB", "quantity": 1},
            {"id": 9002, "supplierArticle": "SKU-WB", "quantity": 1},
        ]
        self.supply_id = "WB-GI-TEST"
        self.sticker_b64 = base64.b64encode(_png_bytes()).decode("ascii")
        self.created: list[tuple[str, str]] = []
        self.added: dict[str, list[int]] = {}

    def is_configured(self) -> bool:
        return True

    def fetch_new_assembly_orders(self) -> list[dict]:
        return list(self.orders)

    def fetch_orders_by_ids(self, order_ids: list[int]) -> list[dict]:
        want = {int(x) for x in order_ids}
        return [o for o in self.orders if int(o["id"]) in want]

    def create_supply(self, name: str) -> str:
        sid = self.supply_id if not getattr(self, "created", None) else f"WB-GI-{len(self.created) + 1}"
        self.created.append((sid, name))
        return sid

    def add_orders_to_supply(self, supply_id: str, order_ids: list[int]) -> None:
        self.added.setdefault(supply_id, []).extend(int(x) for x in order_ids)

    def fetch_order_stickers_png(self, order_ids: list[int]) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        for oid in order_ids:
            out[int(oid)] = {
                "barcode": f"WB{oid}",
                "partA": "111",
                "partB": "222",
                "png_b64": self.sticker_b64,
            }
        return out

    def fetch_supply_order_ids(self, supply_id: str) -> list[int]:
        mapping = {
            self.supply_id: [9001],
            "WB-GI-A": [9001],
            "WB-GI-B": [9002],
        }
        if supply_id not in mapping:
            raise AssertionError(f"unexpected supply {supply_id}")
        return list(mapping[supply_id])

    def list_open_supplies(self) -> list[dict]:
        return [
            {"id": self.supply_id, "name": "Test supply", "done": False},
            {"id": "WB-GI-A", "name": "Supply A", "done": False},
            {"id": "WB-GI-B", "name": "Supply B", "done": False},
        ]


def _catalog(db_url: str) -> tuple[CatalogRepository, str]:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo, db_url


def test_create_wb_job_started(db_url: str, tmp_path) -> None:
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
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_packing")
    packing.init_schema()
    adapter = FakeWbAdapter()

    job = create_wb_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        order_substatus="STARTED",
        item_limit=None,
        packer_user_ids=[7],
        created_by_user_id=1,
    )

    assert job.marketplace == "wildberries"
    assert job.line_total == 2
    assert job.supply_id == "WB-GI-TEST"
    assert len(adapter.created) == 1
    assert adapter.added["WB-GI-TEST"] == [9001, 9002]
    assert all(line.place_total == 1 for line in job.lines)


def test_started_splits_supplies_by_warehouse_and_legal_entity(db_url: str, tmp_path) -> None:
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
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_split")
    packing.init_schema()
    adapter = FakeWbAdapter()
    adapter.orders = [
        {"id": 9001, "article": "SKU-WB", "warehouseId": 11, "options": {"isB2B": False}},
        {"id": 9002, "article": "SKU-WB", "warehouseId": 22, "options": {"isB2B": False}},
        {"id": 9003, "article": "SKU-WB", "warehouseId": 11, "options": {"isB2B": True}},
        {"id": 9004, "article": "SKU-WB", "warehouseId": 11, "options": {"isB2B": False}},
    ]

    job = create_wb_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        order_substatus="STARTED",
        item_limit=None,
        packer_user_ids=[7],
        created_by_user_id=1,
    )

    assert job.line_total == 4
    assert len(adapter.created) == 3
    assert adapter.added["WB-GI-TEST"] == [9001, 9004]
    assert adapter.added["WB-GI-2"] == [9002]
    assert adapter.added["WB-GI-3"] == [9003]
    assert "юрлица" in adapter.created[2][1]
    assert job.sheet_title.startswith("Объединено:")
    assert "WB-GI-TEST" in job.sheet_title


def test_one_supply_per_warehouse_for_retail_and_one_for_legal(db_url: str, tmp_path) -> None:
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
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_b2b")
    packing.init_schema()
    adapter = FakeWbAdapter()
    adapter.orders = [
        {"id": 9101, "article": "SKU-WB", "warehouseId": 11, "offices": ["Новосибирск"], "options": {"isB2B": True}},
        {"id": 9102, "article": "SKU-WB", "warehouseId": 11, "offices": ["Подольск"], "options": {"isB2B": True}},
        {"id": 9103, "article": "SKU-WB", "warehouseId": 11, "offices": ["Подольск"], "options": {"isB2B": True}},
    ]

    job = create_wb_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        order_substatus="STARTED",
        item_limit=None,
        packer_user_ids=[7],
        created_by_user_id=1,
    )

    assert job.line_total == 3
    assert len(adapter.created) == 2
    assert adapter.added["WB-GI-TEST"] == [9101]
    assert adapter.added["WB-GI-2"] == [9102, 9103]
    assert all(packing.read_line_pdf(job.id, line.id).startswith(b"%PDF") for line in job.lines)


def test_create_wb_job_merged_ready_to_ship(db_url: str, tmp_path) -> None:
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
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_packing_merged")
    packing.init_schema()
    adapter = FakeWbAdapter()

    job = create_wb_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        order_substatus="READY_TO_SHIP",
        item_limit=None,
        packer_user_ids=[7],
        created_by_user_id=1,
        supply_ids=["WB-GI-A", "WB-GI-B"],
    )

    assert job.line_total == 2
    assert {line.order_id for line in job.lines} == {"9001", "9002"}
    assert job.supply_id == "WB-GI-A"
    assert job.sheet_title == "Объединено: WB-GI-A + WB-GI-B"


def test_wb_preview_and_create_routes(db_url: str, tmp_path) -> None:
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
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "wb_http")
    packing.init_schema()
    packer = WarehouseUserRow(
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
    adapter = FakeWbAdapter()
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
    client = TestClient(app)

    preview = client.get(
        "/api/warehouse/fbs-packing/preview",
        params={"marketplace": "wildberries", "order_substatus": "STARTED"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["count"] == 2

    supplies = client.get("/api/warehouse/fbs-packing/wb/supplies")
    assert supplies.status_code == 200
    assert supplies.json()["supplies"][0]["id"] == "WB-GI-TEST"

    created = client.post(
        "/api/warehouse/fbs-packing/jobs",
        json={
            "marketplace": "wildberries",
            "order_substatus": "READY_TO_SHIP",
            "supply_id": "WB-GI-TEST",
            "packer_user_ids": [7],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["job"]["marketplace"] == "wildberries"
    assert created.json()["job"]["line_total"] == 1

    preview_merged = client.get(
        "/api/warehouse/fbs-packing/preview",
        params={
            "marketplace": "wildberries",
            "order_substatus": "READY_TO_SHIP",
            "supply_ids": ["WB-GI-A", "WB-GI-B"],
        },
    )
    assert preview_merged.status_code == 200, preview_merged.text
    assert preview_merged.json()["count"] == 2

    created_merged = client.post(
        "/api/warehouse/fbs-packing/jobs",
        json={
            "marketplace": "wildberries",
            "order_substatus": "READY_TO_SHIP",
            "supply_ids": ["WB-GI-A", "WB-GI-B"],
            "packer_user_ids": [7],
        },
    )
    assert created_merged.status_code == 200, created_merged.text
    merged_job = created_merged.json()["job"]
    assert merged_job["line_total"] == 2
    assert merged_job["supply_id"] == "WB-GI-A"
    assert merged_job["sheet_title"] == "Объединено: WB-GI-A + WB-GI-B"
