"""Волна FBS: привязка заказов при отгрузке и списание без MAIN."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.fbs_packing_repository import (
    JOB_STATUS_DONE,
    FbsPackingJob,
    FbsPackingLine,
    LINE_DONE,
)
from app.warehouse_orders_repository import ORDER_IN_WAVE, ORDER_PACKED, ORDER_SHIPPED
from app.warehouse_wave import attach_packing_job_to_orders, ensure_packing_job_attached
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
