"""Волна FBS: привязка заказов при отгрузке и списание без MAIN."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.fbs_packing_repository import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DONE,
    FbsPackingJob,
    FbsPackingLine,
    FbsPackingRepository,
    LINE_DONE,
)
from app.warehouse_orders_repository import ORDER_CANCELLED, ORDER_IN_WAVE, ORDER_PACKED, ORDER_SHIPPED
from app.warehouse_users_repository import WarehouseUserRow
from app.warehouse_wave import (
    annotate_jobs_with_wms_ship_status,
    attach_packing_job_to_orders,
    ensure_packing_job_attached,
)
from app.web.warehouse_wms_routes import register_warehouse_wms_routes
from tests.test_wms_orders import _wms_stack


def _add_packing_job(engine, *, status: str, order_id: str, sku: str) -> int:
    FbsPackingJob.metadata.create_all(engine)
    with Session(engine) as session:
        job = FbsPackingJob(
            marketplace="wildberries",
            order_substatus="READY_TO_SHIP",
            build_list=False,
            status=status,
        )
        session.add(job)
        session.flush()
        session.add(
            FbsPackingLine(
                job_id=int(job.id),
                seq=1,
                sku=sku,
                order_id=order_id,
                product_name=sku,
                status=LINE_DONE,
            )
        )
        session.commit()
        return int(job.id)


def test_attach_sets_in_wave(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="5749378779",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": "A"}],
    )
    job = {
        "id": 76,
        "marketplace": "wildberries",
        "status": "open",
        "lines": [{"order_id": "5749378779", "sku": "SKU-A", "product_name": "A"}],
    }
    attach_packing_job_to_orders(stack["orders"], job, stack["wh_id"])
    order = stack["orders"].get_by_posting("wildberries", "5749378779")
    assert order.status == ORDER_IN_WAVE
    assert order.packing_job_id == 76


def test_wave_ship_attaches_missing_job_link(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-A", 5, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="WB-OPEN-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": "A"}],
    )
    job_id = _add_packing_job(
        stack["inventory"].engine, status="open", order_id="WB-OPEN-1", sku="SKU-A"
    )
    assert stack["orders"].get_by_posting("wildberries", "WB-OPEN-1").packing_job_id is None

    shipped = stack["shipments"].create_from_packing_job(job_id, post=True)
    assert shipped.status == "posted"
    order = stack["orders"].get_by_posting("wildberries", "WB-OPEN-1")
    assert order.status == ORDER_SHIPPED
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-A", bin_id=stack["main_bin"].id) == 4


def test_wave_ship_without_main_still_marks_shipped(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-B", 0, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="WB-NO-MAIN",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 2, "name": "B"}],
    )
    job_id = _add_packing_job(
        stack["inventory"].engine, status=JOB_STATUS_DONE, order_id="WB-NO-MAIN", sku="SKU-B"
    )
    ensure_packing_job_attached(stack["orders"], stack["inventory"].engine, job_id, stack["wh_id"])
    packed = stack["orders"].get_by_posting("wildberries", "WB-NO-MAIN")
    assert packed.status == ORDER_PACKED

    shipped = stack["shipments"].create_from_packing_job(job_id, post=True)
    assert shipped.status == "posted"
    assert any("MAIN не списан" in w for w in shipped.warnings)
    order = stack["orders"].get_by_posting("wildberries", "WB-NO-MAIN")
    assert order.status == ORDER_SHIPPED
    assert stack["storage"].get_stock(stack["wh_id"], "SKU-B", bin_id=stack["main_bin"].id) == 0


def test_annotate_jobs_with_wms_ship_status() -> None:
    jobs = [
        {"id": 1, "status": "done"},
        {"id": 2, "status": "done"},
        {"id": 3, "status": "cancelled"},
        {"id": 4, "status": "open"},
    ]
    flags = {
        1: {"shipped": False, "pending_count": 2, "order_count": 2, "shipped_count": 0},
        2: {"shipped": True, "pending_count": 0, "order_count": 2, "shipped_count": 2},
        3: {"shipped": False, "pending_count": 1, "order_count": 1, "shipped_count": 0},
    }
    out = annotate_jobs_with_wms_ship_status(jobs, flags)
    assert out[0]["wms_shipped"] is False
    assert out[0]["can_ship"] is True
    assert out[1]["wms_shipped"] is True
    assert out[1]["can_ship"] is False
    assert out[2]["wms_shipped"] is False
    assert out[2]["can_ship"] is False
    # Без привязанных заказов кнопку отгрузки оставляем только у open/in_progress.
    assert out[3]["wms_shipped"] is False
    assert out[3]["can_ship"] is True
    done_empty = annotate_jobs_with_wms_ship_status([{"id": 5, "status": "done"}], {})
    assert done_empty[0]["can_ship"] is False


def test_packing_job_ship_flags(db_url: str) -> None:
    stack = _wms_stack(db_url)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="FLAG-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": "A"}],
    )
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="FLAG-2",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 1, "name": "B"}],
    )
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="FLAG-3",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-C", "quantity": 1, "name": "C"}],
    )
    o1 = stack["orders"].get_by_posting("wildberries", "FLAG-1")
    o2 = stack["orders"].get_by_posting("wildberries", "FLAG-2")
    o3 = stack["orders"].get_by_posting("wildberries", "FLAG-3")
    stack["orders"].set_packing_job([o1.id, o2.id], 11)
    stack["orders"].set_packing_job([o3.id], 12)
    stack["orders"].mark_shipped("wildberries", "FLAG-2")
    stack["orders"].set_status(o3.id, ORDER_CANCELLED)

    flags = stack["orders"].packing_job_ship_flags([11, 12, 13])
    assert flags[11]["shipped"] is False
    assert flags[11]["can_ship"] is True
    assert flags[11]["pending_count"] == 1
    assert flags[11]["shipped_count"] == 1
    assert flags[12]["shipped"] is False
    assert flags[12]["can_ship"] is False
    assert flags[12]["order_count"] == 1
    assert flags[13]["shipped"] is False
    assert flags[13]["can_ship"] is True

    stack["orders"].mark_shipped("wildberries", "FLAG-1")
    after = stack["orders"].packing_job_ship_flags([11])
    assert after[11]["shipped"] is True
    assert after[11]["can_ship"] is False


def test_wave_ship_already_shipped_via_sync(db_url: str) -> None:
    """Синк МП уже пометил заказы shipped, packing_job_id пуст — волна закрывается без ошибки."""
    stack = _wms_stack(db_url)
    stack["storage"].set_stock(stack["wh_id"], "SKU-Z", 0, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="WB-ALREADY",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-Z", "quantity": 1, "name": "Z"}],
    )
    stack["orders"].mark_shipped("wildberries", "WB-ALREADY")
    assert stack["orders"].get_by_posting("wildberries", "WB-ALREADY").packing_job_id is None
    job_id = _add_packing_job(
        stack["inventory"].engine, status=JOB_STATUS_DONE, order_id="WB-ALREADY", sku="SKU-Z"
    )
    shipped = stack["shipments"].create_from_packing_job(job_id, post=True)
    assert shipped.status == "posted"
    assert any("уже были отгружены" in w for w in shipped.warnings)
    order = stack["orders"].get_by_posting("wildberries", "WB-ALREADY")
    assert order.status == ORDER_SHIPPED
    assert order.packing_job_id == job_id
    flags = stack["orders"].packing_job_ship_flags([job_id])
    assert flags[job_id]["shipped"] is True


def test_pick_waves_api_ship_flag(db_url: str, tmp_path) -> None:
    stack = _wms_stack(db_url)
    packing = FbsPackingRepository(db_url, files_data_dir=tmp_path / "fbs_packing")
    packing.init_schema()
    open_id = _add_packing_job(
        stack["inventory"].engine, status="open", order_id="PW-OPEN", sku="SKU-A"
    )
    done_id = _add_packing_job(
        stack["inventory"].engine, status=JOB_STATUS_DONE, order_id="PW-DONE", sku="SKU-B"
    )
    cancelled_id = _add_packing_job(
        stack["inventory"].engine,
        status=JOB_STATUS_CANCELLED,
        order_id="PW-CAN",
        sku="SKU-C",
    )
    stack["storage"].set_stock(stack["wh_id"], "SKU-B", 5, skip_recalc=True)
    stack["orders"].upsert_from_posting(
        source="wildberries",
        posting_id="PW-DONE",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-B", "quantity": 1, "name": "B"}],
    )
    stack["shipments"].create_from_packing_job(done_id, post=True)

    app = FastAPI()

    def require_warehouse_user() -> WarehouseUserRow:
        return WarehouseUserRow(
            id=1,
            login="admin",
            display_name="Admin",
            group_id=None,
            group_name="",
            telegram_nick="",
            is_admin=True,
            is_active=True,
            permissions={},
            created_at_ts=0,
            updated_at_ts=0,
        )

    register_warehouse_wms_routes(
        app,
        stack["orders"],
        stack["shipments"],
        None,
        None,
        stack["storage"],
        None,
        packing,
        require_warehouse_user,
    )
    resp = TestClient(app).get("/api/warehouse/pick-waves")
    assert resp.status_code == 200
    by_id = {int(row["id"]): row for row in resp.json()["jobs"]}
    assert by_id[open_id]["wms_shipped"] is False
    assert by_id[open_id]["can_ship"] is True
    assert by_id[done_id]["wms_shipped"] is True
    assert by_id[done_id]["can_ship"] is False
    assert by_id[cancelled_id]["wms_shipped"] is False
    assert by_id[cancelled_id]["can_ship"] is False
