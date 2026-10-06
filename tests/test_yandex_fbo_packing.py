"""Задания FBO YM: FBY API, разбор CARGO_UNITS, назначение в грузоместа."""

from __future__ import annotations

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.warehouse_users_repository import WarehouseUsersRepository
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.web.warehouse_yandex_fbo_routes import register_warehouse_yandex_fbo_routes
from app.yandex_fbo_api import parse_cargo_units_pdf
from app.yandex_fbo_repository import YandexFboRepository
from app.yandex_fbo_service import create_yandex_fbo_job


REQUEST_ID = 10180422
CARGO_A = "B02540C0DF00035BC1CE"
CARGO_B = "B02540C0DF00035BC1CF"


def _pdf(*codes: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(220, 90))
    for index, code in enumerate(codes):
        if index:
            c.showPage()
        c.setFont("Helvetica", 10)
        c.drawString(10, 50, f"Korobka {index + 1}/{len(codes)}")
        c.drawString(10, 30, code)
    c.save()
    return buf.getvalue()


class FakeYandexFboApi:
    def __init__(
        self,
        *,
        pdf: bytes | None = None,
        items: list[dict] | None = None,
        supply: dict | None = None,
    ) -> None:
        self.pdf = pdf if pdf is not None else _pdf(CARGO_A)
        self.items = items if items is not None else [
            {
                "sku": "SS694",
                "name": "Shine Systems FastQuartz",
                "planned_qty": 40,
                "barcodes": ["4673746970690"],
            }
        ]
        self.supply = supply or {
            "request_id": REQUEST_ID,
            "marketplace_request_id": "33388649",
            "warehouse_request_id": "0001142103",
            "parent_request_id": 10180419,
            "vrc_id": 10180419,
            "vrc_label": "ВРЦ-10180419",
            "display_id": "33388649 / ВРЦ-10180419",
            "type": "SUPPLY",
            "subtype": "VIRTUAL_DISTRIBUTION_CENTER_CHILD",
            "is_child": True,
            "status": "ACCEPTED_AT_WAREHOUSE",
            "status_hidden": False,
            "planned_qty": 40,
            "warehouse_name": "МО Софьино",
            "transit_warehouse": "ПВЗ Одоевского",
            "transit_at": "2026-10-07T02:00:00Z",
            "accept_at": "2026-10-16T06:00:00Z",
            "updated_at": "2026-10-06T10:00:00Z",
        }
        self.downloads = 0

    def is_configured(self) -> bool:
        return True

    def list_child_supplies(self) -> list[dict]:
        return [dict(self.supply)]

    def get_supply(self, request_id: int) -> dict:
        if int(request_id) != int(self.supply["request_id"]):
            raise ValueError("Заявка FBY не найдена")
        return dict(self.supply)

    def get_items(self, request_id: int) -> list[dict]:
        self.get_supply(request_id)
        return [dict(item) for item in self.items]

    def download_cargo_units_pdf(self, request_id: int) -> bytes:
        self.get_supply(request_id)
        if not self.pdf:
            raise ValueError("PDF грузомест ещё недоступен")
        self.downloads += 1
        return self.pdf


def _catalog(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    repo.create_product(
        {
            "name": "FastQuartz",
            "sku": "SS694",
            "code": "00012",
            "is_kit": False,
            "barcodes": [{"barcode": "4673746970690", "label": "", "group": ""}],
            "boxes": [{"barcode": "BOX-SS694-6", "quantity": 6}],
            "components": [],
        }
    )
    repo.create_product(
        {
            "name": "Другой товар",
            "sku": "SS700",
            "code": "00013",
            "is_kit": False,
            "barcodes": [{"barcode": "4600000000001", "label": "", "group": ""}],
            "components": [],
        }
    )
    return repo


def _users(db_url: str) -> tuple[WarehouseUsersRepository, int]:
    users = WarehouseUsersRepository(db_url)
    users.init_schema()
    worker = users.create_user(login="packer", password="secret", display_name="Упаковщик")
    return users, int(worker.id)


def _app(db_url: str, tmp_path, api: FakeYandexFboApi, catalog, users, packer_id: int):
    packing = YandexFboRepository(db_url, files_data_dir=tmp_path / "ym_fbo")
    packing.init_schema()
    actor_user = users.get_by_id(packer_id)
    app = FastAPI()
    register_warehouse_yandex_fbo_routes(
        app,
        packing,
        catalog,
        users,
        None,
        lambda: actor_user,
        lambda: TasksApiActor(user=actor_user, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/yandex-fbo-packing",),
        api_factory=lambda: api,
    )
    return app, packing


def test_parse_cargo_units_pdf_pages() -> None:
    pages = parse_cargo_units_pdf(_pdf(CARGO_A, CARGO_B))
    assert [page.cargo_code for page in pages] == [CARGO_A, CARGO_B]
    assert [page.page_index for page in pages] == [0, 1]


def test_parse_cargo_units_pdf_missing_code() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 80))
    c.setFont("Helvetica", 10)
    c.drawString(10, 40, "no cargo here")
    c.save()
    try:
        parse_cargo_units_pdf(buf.getvalue())
    except ValueError as exc:
        assert "не найден код грузоместа" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_job_rejects_missing_items_and_pdf(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    packing = YandexFboRepository(db_url, files_data_dir=tmp_path / "ym")
    packing.init_schema()
    users, packer_id = _users(db_url)
    api = FakeYandexFboApi(items=[], pdf=_pdf(CARGO_A))
    try:
        create_yandex_fbo_job(
            api=api,
            catalog=catalog,
            packing_repo=packing,
            request_id=REQUEST_ID,
            packer_user_ids=[packer_id],
            created_by_user_id=packer_id,
        )
    except ValueError as exc:
        assert "Товары" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    api.items = [{"sku": "SS694", "name": "X", "planned_qty": 40, "barcodes": []}]
    api.pdf = b""
    try:
        create_yandex_fbo_job(
            api=api,
            catalog=catalog,
            packing_repo=packing,
            request_id=REQUEST_ID,
            packer_user_ids=[packer_id],
            created_by_user_id=packer_id,
        )
    except ValueError as exc:
        assert "PDF" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_assign_resolve_and_labels(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    users, packer_id = _users(db_url)
    api = FakeYandexFboApi(
        pdf=_pdf(CARGO_A, CARGO_B),
        items=[
            {
                "sku": "SS694",
                "name": "FastQuartz",
                "planned_qty": 40,
                "barcodes": ["4673746970690"],
            },
            {
                "sku": "SS700",
                "name": "Другой",
                "planned_qty": 10,
                "barcodes": ["4600000000001"],
            },
        ],
    )
    app, packing = _app(db_url, tmp_path, api, catalog, users, packer_id)
    client = TestClient(app)

    listed = client.get("/api/warehouse/marketplaces/yandex-fbo/supplies")
    assert listed.status_code == 200, listed.text
    row = listed.json()["supplies"][0]
    assert row["marketplace_request_id"] == "33388649"
    assert row["has_job"] is False
    assert "ВРЦ-10180419" in row["display_id"]

    preview = client.get(f"/api/warehouse/marketplaces/yandex-fbo/supplies/{REQUEST_ID}")
    assert preview.status_code == 200
    assert preview.json()["items"][0]["sku"] == "SS694"

    created = client.post(
        "/api/warehouse/marketplaces/yandex-fbo/jobs",
        json={"request_id": REQUEST_ID, "packer_user_ids": [packer_id]},
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    job_id = job["id"]
    assert job["marketplace"] == "yandex"
    assert job["line_total"] == 50
    assert job["cargo_total"] == 2
    assert job["display_id"].startswith("33388649")

    duplicate = client.post(
        "/api/warehouse/marketplaces/yandex-fbo/jobs",
        json={"request_id": REQUEST_ID, "packer_user_ids": [packer_id]},
    )
    assert duplicate.status_code == 400
    assert "уже создано" in duplicate.json()["detail"]

    mine = client.get("/api/v1/yandex-fbo-packing/my")
    assert mine.status_code == 200
    assert mine.json()["jobs"][0]["id"] == job_id

    packed = client.get(f"/api/v1/yandex-fbo-packing/jobs/{job_id}/pack")
    assert packed.status_code == 200
    products = packed.json()["job"]["products"]
    product_a = next(item for item in products if item["sku"] == "SS694")
    product_b = next(item for item in products if item["sku"] == "SS700")

    by_sku = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/resolve",
        json={"barcode": "SS694"},
    )
    assert by_sku.status_code == 200
    assert by_sku.json()["kind"] == "product"
    assert by_sku.json()["product"]["sku"] == "SS694"

    by_ean = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/resolve",
        json={"barcode": "4673746970690"},
    )
    assert by_ean.status_code == 200
    assert by_ean.json()["product"]["id"] == product_a["id"]

    by_box = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/resolve",
        json={"barcode": "BOX-SS694-6"},
    )
    assert by_box.status_code == 200
    assert by_box.json()["product"]["sku"] == "SS694"

    cargo_scan = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/resolve",
        json={"barcode": CARGO_A.lower()},
    )
    assert cargo_scan.status_code == 200
    assert cargo_scan.json()["kind"] == "cargo"

    overflow = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/assign",
        json={"product_id": product_a["id"], "quantity": 41, "cargo_code": CARGO_A},
    )
    assert overflow.status_code == 400
    assert "остаток" in overflow.json()["detail"]

    unknown_cargo = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/assign",
        json={"product_id": product_a["id"], "quantity": 1, "cargo_code": "B00000000000"},
    )
    assert unknown_cargo.status_code == 400

    first = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/assign",
        json={"product_id": product_a["id"], "quantity": 10, "cargo_code": CARGO_A},
    )
    assert first.status_code == 200, first.text
    assert first.json()["job"]["status"] == "in_progress"
    assert first.json()["job"]["line_done"] == 10

    second = client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{job_id}/assign",
        json={"product_id": product_b["id"], "quantity": 4, "cargo_code": CARGO_A},
    )
    assert second.status_code == 200
    cargo = next(
        item for item in second.json()["job"]["cargoes"] if item["cargo_code"] == CARGO_A
    )
    assert cargo["assigned_qty"] == 14
    assert {item["sku"] for item in cargo["items"]} == {"SS694", "SS700"}

    labels = client.get(f"/api/v1/yandex-fbo-packing/jobs/{job_id}/labels.pdf")
    assert labels.status_code == 200
    assert labels.content.startswith(b"%PDF")
    assert labels.content == api.pdf
    assert len(PdfReader(io.BytesIO(labels.content)).pages) == 2
    assert api.downloads == 1

    collide_api = FakeYandexFboApi(
        pdf=_pdf(CARGO_A),
        items=[
            {"sku": "SS694", "name": "A", "planned_qty": 1, "barcodes": ["SAME"]},
            {"sku": "SS700", "name": "B", "planned_qty": 1, "barcodes": ["SAME"]},
        ],
        supply={**api.supply, "request_id": 999001, "marketplace_request_id": "999001"},
    )
    collide_app, _ = _app(db_url, tmp_path / "collide", collide_api, catalog, users, packer_id)
    collide_client = TestClient(collide_app)
    collide_job = collide_client.post(
        "/api/warehouse/marketplaces/yandex-fbo/jobs",
        json={"request_id": 999001, "packer_user_ids": [packer_id]},
    )
    assert collide_job.status_code == 200, collide_job.text
    collide_id = collide_job.json()["job"]["id"]
    clash = collide_client.post(
        f"/api/v1/yandex-fbo-packing/jobs/{collide_id}/resolve",
        json={"barcode": "SAME"},
    )
    assert clash.status_code == 400
    assert "несколькими товарами" in clash.json()["detail"]

    assignees = client.put(
        f"/api/warehouse/marketplaces/yandex-fbo/jobs/{job_id}/assignees",
        json={"all": True},
    )
    assert assignees.status_code == 200
    assert packer_id in assignees.json()["job"]["packer_user_ids"]

    cancelled = client.post(f"/api/warehouse/marketplaces/yandex-fbo/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert packing.user_can_pack(job_id, packer_id)
    mine_after = client.get("/api/v1/yandex-fbo-packing/my")
    assert all(item["id"] != job_id for item in mine_after.json()["jobs"])
