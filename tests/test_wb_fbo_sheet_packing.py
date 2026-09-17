"""FBO WB new: Excel кабинета, печать ШК, присвоение товара, выгрузка грузомест."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_fbo_sheet_repository import BOX_ASSIGNED, BOX_PRINTED, WbFboSheetRepository
from app.wb_fbo_sheet_xlsx import fill_boxes_xlsx, parse_boxes_xlsx, parse_goods_xlsx
from app.web.warehouse_tasks_api_auth import TasksApiActor
from app.web.warehouse_wb_fbo_sheet_routes import register_warehouse_wb_fbo_sheet_routes

_GOODS_ROWS = (
    ("4673746970607", 750, "SS958"),
    ("4673746971086", 700, "SS959"),
    ("4673746971437", 700, "SS960"),
    ("4673746971475", 700, "SS961"),
)


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _goods_xlsx() -> bytes:
    return _workbook_bytes(
        [["Баркод", "Количество, шт", "Артикул поставщика"], *[list(item) for item in _GOODS_ROWS]]
    )


def _boxes_xlsx(*, count: int = 50) -> bytes:
    rows: list[list[object]] = [
        [
            "Баркод товара",
            "Кол-во товаров",
            "ШК короба",
            "Срок годности",
            "ШК короба для печати в стороннем сервисе",
        ]
    ]
    for index in range(count):
        box_id = str(4662171 + index)
        rows.append(["", 0, box_id, "", f"$Ts;0;0;1;box{index};TAS"])
    return _workbook_bytes(rows)


def test_parse_example_wb_tables() -> None:
    goods = parse_goods_xlsx(_goods_xlsx())
    boxes = parse_boxes_xlsx(_boxes_xlsx())
    assert [item.barcode for item in goods] == [
        "4673746970607",
        "4673746971086",
        "4673746971437",
        "4673746971475",
    ]
    assert goods[0].qty == 750
    assert goods[0].sku == "SS958"
    assert len(boxes) == 50
    assert boxes[0].box_id == "4662171"
    assert boxes[0].package_code.startswith("$Ts;")
    assert boxes[0].product_barcode == ""
    assert boxes[0].qty == 0


def test_fill_boxes_xlsx_writes_product_and_qty() -> None:
    original = _boxes_xlsx()
    boxes = parse_boxes_xlsx(original)
    filled = fill_boxes_xlsx(
        original,
        [
            {
                "box_id": boxes[0].box_id,
                "product_barcode": "4673746970607",
                "item_qty": 100,
            }
        ],
    )
    parsed = parse_boxes_xlsx(filled)
    assert parsed[0].product_barcode == "4673746970607"
    assert parsed[0].qty == 100
    assert parsed[1].product_barcode == ""
    assert parsed[1].qty == 0
    assert parsed[0].package_code == boxes[0].package_code

    orig_wb = load_workbook(BytesIO(original), data_only=True)
    filled_wb = load_workbook(BytesIO(filled), data_only=True)
    try:
        orig_headers = next(orig_wb.active.iter_rows(min_row=1, max_row=1, values_only=True))
        filled_headers = next(filled_wb.active.iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        orig_wb.close()
        filled_wb.close()
    assert orig_headers == filled_headers
    assert orig_headers[0] == "Баркод товара"
    assert "ШК короба" in orig_headers


def _catalog(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    repo.create_product(
        {
            "name": "SS958",
            "sku": "SS958",
            "code": "00081",
            "is_kit": False,
            "barcodes": [{"barcode": "4673746970607", "label": "", "group": ""}],
            "boxes": [{"barcode": "BOX-SS958-100", "quantity": 100}],
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


def _client(db_url: str, tmp_path, catalog: CatalogRepository):
    packing = WbFboSheetRepository(db_url, files_data_dir=tmp_path / "wb_fbo_sheet")
    packing.init_schema()
    packer = _packer()
    app = FastAPI()
    register_warehouse_wb_fbo_sheet_routes(
        app,
        packing,
        catalog,
        SimpleNamespace(list_assignee_picker=lambda: [{"id": 7, "display_name": "packer"}]),
        lambda: packer,
        lambda: TasksApiActor(user=packer, via_api_token=False),
        include_manager=True,
        packer_prefixes=("/api/v1/fbo-sheet-packing",),
    )
    return TestClient(app), packing


def test_create_print_assign_and_download_boxes_xlsx(db_url: str, tmp_path) -> None:
    catalog = _catalog(db_url)
    client, packing = _client(db_url, tmp_path, catalog)
    xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    created = client.post(
        "/api/warehouse/marketplaces/wb-fbo-new/jobs",
        data={"packer_user_ids": "[7]", "supply_id": "41357389"},
        files={
            "goods": ("goods.xlsx", _goods_xlsx(), xlsx_type),
            "boxes": ("boxes.xlsx", _boxes_xlsx(), xlsx_type),
        },
    )
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["pcs_plan"] == 2850
    assert job["box_total"] == 50
    assert job["box_assigned"] == 0
    job_id = job["id"]

    printed = client.post(
        f"/api/v1/fbo-sheet-packing/jobs/{job_id}/print-boxes",
        json={"count": 2},
    )
    assert printed.status_code == 200, printed.text
    assert len(printed.json()["boxes"]) == 2
    assert printed.json()["boxes"][0]["status"] == BOX_PRINTED
    assert printed.json()["pdf_base64"]
    first_box = printed.json()["boxes"][0]["box_id"]
    second_box = printed.json()["boxes"][1]["box_id"]

    resolved = client.post(
        f"/api/v1/fbo-sheet-packing/jobs/{job_id}/resolve",
        json={"barcode": "BOX-SS958-100"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["kind"] == "product"
    assert resolved.json()["suggested_qty"] == 100
    assert resolved.json()["product"]["barcode"] == "4673746970607"

    assigned = client.post(
        f"/api/v1/fbo-sheet-packing/jobs/{job_id}/assign",
        json={
            "barcode": first_box,
            "product_barcode": "4673746970607",
            "quantity": 100,
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["box"]["status"] == BOX_ASSIGNED
    assert assigned.json()["qty_warning"] == ""

    leftover = client.post(
        f"/api/v1/fbo-sheet-packing/jobs/{job_id}/assign",
        json={
            "barcode": second_box,
            "product_barcode": "4673746970607",
            "quantity": 37,
        },
    )
    assert leftover.status_code == 200, leftover.text
    assert "37 товара" in leftover.json()["qty_warning"]

    downloaded = client.get(f"/api/warehouse/marketplaces/wb-fbo-new/jobs/{job_id}/boxes.xlsx")
    assert downloaded.status_code == 200
    parsed = parse_boxes_xlsx(downloaded.content)
    by_id = {item.box_id: item for item in parsed}
    assert by_id[first_box].product_barcode == "4673746970607"
    assert by_id[first_box].qty == 100
    assert by_id[second_box].qty == 37
    empty = [item for item in parsed if not item.product_barcode]
    assert len(empty) == 48
    assert packing.get_job(job_id).pcs_assigned == 137
