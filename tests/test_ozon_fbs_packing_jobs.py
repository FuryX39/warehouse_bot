"""Задания FBS-упаковки Ozon: диапазон отправлений без Google Sheets."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.adapters.ozon import OzonAdapter, OzonFbsPosting
from app.catalog_repository import CatalogRepository
from app.config import Settings
from app.crm_repository import CrmRepository
from app.fbs_packing_repository import FbsPackingRepository
from app.fbs_packing_service import create_ozon_packing_job
from app.ozon_fbs_labels import (
    OzonFbsListRow,
    filter_by_posting_range,
    load_ozon_fbs_list_rows,
)
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_fbs_packing_routes import register_warehouse_fbs_packing_routes
from app.web.warehouse_tasks_api_auth import TasksApiActor


def _pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    c.drawString(20, 100, text)
    c.showPage()
    c.save()
    return buf.getvalue()


class FakeOzonAdapter(OzonAdapter):
    def __init__(self) -> None:
        super().__init__(client_id="116948", api_key="test-key", warehouse_id="1")
        self.postings = [
            OzonFbsPosting("P-1", "awaiting_deliver", (("SKU-A", 1),), in_process_at_ts=10),
            OzonFbsPosting("P-2", "awaiting_deliver", (("SKU-A", 2),), in_process_at_ts=20),
            OzonFbsPosting("P-3", "awaiting_deliver", (("SKU-B", 1),), in_process_at_ts=30),
        ]
        self.listed = 0
        self.label_requests: list[list[str]] = []

    def is_configured(self) -> bool:
        return True

    def list_awaiting_shipment_postings(self, statuses=None) -> list[OzonFbsPosting]:
        self.listed += 1
        return list(self.postings)

    def fetch_package_label_by_posting(self, posting_numbers, *, chunk_size: int = 20):
        self.label_requests.append(list(posting_numbers))
        return {str(pn): _pdf(str(pn)) for pn in posting_numbers}, []


def _catalog(tmp_path) -> tuple[CatalogRepository, str]:
    db_url = f"sqlite:///{(tmp_path / 'ozon.db').as_posix()}"
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo, db_url


def test_filter_by_posting_range_inclusive_and_swap() -> None:
    rows = [
        OzonFbsListRow(1, "P-1", "A", 1, "awaiting_deliver"),
        OzonFbsListRow(2, "P-2", "A", 1, "awaiting_deliver"),
        OzonFbsListRow(3, "P-3", "B", 1, "awaiting_deliver"),
    ]
    order = ["P-1", "P-2", "P-3"]
    empty_rows, empty_order = filter_by_posting_range(rows, order)
    assert empty_order == order
    assert [r.posting_number for r in empty_rows] == order

    one_rows, one_order = filter_by_posting_range(rows, order, first_posting="P-2")
    assert one_order == ["P-2"]
    assert [r.posting_number for r in one_rows] == ["P-2"]

    sliced, sliced_order = filter_by_posting_range(
        rows, order, first_posting="P-1", last_posting="P-2"
    )
    assert sliced_order == ["P-1", "P-2"]
    assert [r.posting_number for r in sliced] == ["P-1", "P-2"]

    swapped, swapped_order = filter_by_posting_range(
        rows, order, first_posting="P-3", last_posting="P-2"
    )
    assert swapped_order == ["P-2", "P-3"]

    with pytest.raises(ValueError, match="не найден"):
        filter_by_posting_range(rows, order, first_posting="P-9", last_posting="P-9")


def test_create_ozon_job_range_explodes_qty_without_sheets(tmp_path) -> None:
    catalog, db_url = _catalog(tmp_path)
    catalog.create_product(
        {
            "name": "Ozon товар",
            "sku": "SKU-A",
            "code": "00021",
            "is_kit": False,
            "barcodes": [{"barcode": "OZ-SKU", "label": "", "group": ""}],
            "components": [],
        }
    )
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "ozon_packing")
    packing.init_schema()
    adapter = FakeOzonAdapter()
    settings = Settings(
        telegram_bot_token="t",
        db_url=db_url,
        movement_db_url=db_url,
        ozon_label_rotate_degrees=0,
        fbs_list_sheet_url="https://example.invalid/should-not-be-used",
        google_service_account_file="missing.json",
    )

    job = create_ozon_packing_job(
        adapter=adapter,
        catalog=catalog,
        packing_repo=packing,
        settings=settings,
        packer_user_ids=[7],
        created_by_user_id=1,
        first_posting="P-1",
        last_posting="P-2",
    )

    assert job.marketplace == "ozon"
    assert job.order_substatus == "awaiting_deliver"
    assert job.build_list is False
    assert job.sheet_url == ""
    assert job.line_total == 3
    assert {line.order_id for line in job.lines} == {"P-1", "P-2"}
    assert [line.sku for line in job.lines] == ["SKU-A", "SKU-A", "SKU-A"]
    assert all(packing.read_line_pdf(job.id, line.id).startswith(b"%PDF") for line in job.lines)
    assert adapter.listed == 1


def test_ozon_preview_and_create_routes(tmp_path) -> None:
    catalog, db_url = _catalog(tmp_path)
    catalog.create_product(
        {
            "name": "Ozon товар",
            "sku": "SKU-A",
            "code": "00021",
            "is_kit": False,
            "barcodes": [{"barcode": "OZ-SKU", "label": "", "group": ""}],
            "components": [],
        }
    )
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "ozon_http")
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
    adapter = FakeOzonAdapter()
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
        params={"marketplace": "ozon", "first_posting": "P-1", "last_posting": "P-2"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["marketplace"] == "ozon"
    assert body["orders_count"] == 2
    assert body["available_count"] == 3
    assert [row["posting_number"] for row in body["list_rows"]] == ["P-1", "P-2"]

    created = client.post(
        "/api/warehouse/fbs-packing/jobs",
        json={
            "marketplace": "ozon",
            "first_posting": "P-2",
            "last_posting": "P-3",
            "packer_user_ids": [7],
        },
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["marketplace"] == "ozon"
    assert job["line_total"] == 3
    assert {line["order_id"] for line in job["lines"]} == {"P-2", "P-3"}


def test_load_ozon_rows_missing_posting_raises() -> None:
    adapter = FakeOzonAdapter()
    with pytest.raises(ValueError, match="не найден"):
        load_ozon_fbs_list_rows(adapter, first_posting="NOPE", last_posting="NOPE")
