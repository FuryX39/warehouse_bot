from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.fbs_packing_repository import (
    FbsPackingJob,
    FbsPackingLine,
    FbsPackingRepository,
)
from app.other_marketplace_repository import (
    LINE_DONE,
    LINE_PENDING,
    OtherMarketplaceJob,
    OtherMarketplaceLine,
    OtherMarketplaceRepository,
)
from app.warehouse_productivity_repository import WarehouseProductivityRepository
from app.warehouse_users_repository import WarehouseUsersRepository
from app.wb_fbo_packing_repository import (
    WbFboPackingJob,
    WbFboPackingLine,
    WbFboPackingRepository,
)
from app.wb_fbo_sheet_repository import (
    WbFboSheetBox,
    WbFboSheetBoxItem,
    WbFboSheetJob,
    WbFboSheetRepository,
)


def _init(db_url, tmp_path):
    users = WarehouseUsersRepository(db_url)
    users.init_schema()
    FbsPackingRepository(db_url, files_data_dir=tmp_path / "fbs").init_schema()
    WbFboPackingRepository(db_url, files_data_dir=tmp_path / "fbo").init_schema()
    WbFboSheetRepository(db_url, files_data_dir=tmp_path / "sheet").init_schema()
    other = OtherMarketplaceRepository(db_url)
    other.init_schema()
    return users, other


def test_productivity_aggregates_all_packing_types_and_filters(db_url, tmp_path):
    users, _ = _init(db_url, tmp_path)
    worker = users.create_user(
        login="packer",
        password="secret",
        display_name="Анна Упаковщик",
    )
    event_ts = int(
        datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")).timestamp()
    )
    with Session(users.engine) as session:
        fbs_job = FbsPackingJob(
            marketplace="wildberries",
            order_substatus="STARTED",
            build_list=True,
            require_cis=False,
            supply_id="SUP-FBS",
            transfer_number="TR-1",
            status="done",
            created_by_user_id=None,
            sheet_url="",
            sheet_title="",
            merged_label_stored_name="",
            warnings_json="[]",
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(fbs_job)
        session.flush()
        for seq in (1, 2):
            session.add(
                FbsPackingLine(
                    job_id=fbs_job.id,
                    seq=seq,
                    sku=f"SKU-{seq}",
                    product_id=None,
                    product_name="Товар",
                    order_id=f"O-{seq}",
                    box_id=None,
                    place_index=1,
                    place_total=1,
                    scan_keys_json="[]",
                    label_stored_name="",
                    status="done",
                    printed_at_ts=event_ts,
                    done_at_ts=event_ts,
                    done_by_user_id=worker.id,
                    cis_raw="",
                    cis_key="",
                    cis_gtin="",
                )
            )

        fbo_job = WbFboPackingJob(
            supply_id="WB-77",
            warehouse_name="Коледино",
            city="Москва",
            seller_name="Продавец",
            plan_date="2026-10-03",
            pallet_count=1,
            supply_qr_code="",
            supply_qr_stored_name="",
            pallet_sheets_stored_name="",
            box_labels_stored_name="",
            status="done",
            created_by_user_id=None,
            warnings_json="[]",
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(fbo_job)
        session.flush()
        session.add(
            WbFboPackingLine(
                job_id=fbo_job.id,
                seq=1,
                sku="FBO-1",
                product_id=None,
                product_name="Короб",
                box_human_id="1",
                package_code="PKG",
                item_qty=6,
                scan_keys_json="[]",
                label_stored_name="",
                status="done",
                printed_at_ts=event_ts,
                done_at_ts=event_ts,
                done_by_user_id=worker.id,
            )
        )

        sheet_job = WbFboSheetJob(
            supply_id="SHEET-5",
            warehouse_name="Электросталь",
            seller_name="Продавец",
            plan_date="2026-10-04",
            box_type="Короб",
            goods_stored_name="goods.xlsx",
            boxes_stored_name="boxes.xlsx",
            status="in_progress",
            created_by_user_id=None,
            warnings_json="[]",
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(sheet_job)
        session.flush()
        box = WbFboSheetBox(
            job_id=sheet_job.id,
            seq=1,
            box_human_id="BOX-1",
            package_code="BOX-CODE",
            product_barcode="4600",
            item_qty=4,
            status="assigned",
            printed_at_ts=event_ts,
            assigned_at_ts=event_ts,
            assigned_by_user_id=worker.id,
            pallet_id=None,
        )
        session.add(box)
        session.flush()
        box_id = int(box.id)
        session.add(
            WbFboSheetBoxItem(
                job_id=sheet_job.id,
                box_id=box.id,
                seq=1,
                product_barcode="4600",
                item_qty=4,
                expiry="",
                assigned_at_ts=event_ts,
                assigned_by_user_id=worker.id,
            )
        )

        other_job = OtherMarketplaceJob(
            platform="vseinstrumenti",
            order_number="VI-42",
            transfer_number="TR-VI",
            supplier="ООО Поставщик",
            delivery_date="2026-10-05",
            purchase_status="Закуплено",
            source_filename="vi.xlsx",
            status="done",
            created_by_user_id=None,
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(other_job)
        session.flush()
        session.add(
            OtherMarketplaceLine(
                job_id=other_job.id,
                seq=1,
                sku="VI-1",
                product_id=None,
                product_name="Инструмент",
                excel_barcode="123",
                quantity=3,
                picked_qty=3,
                require_cis=False,
                status="done",
                done_at_ts=event_ts,
                done_by_user_id=worker.id,
            )
        )
        session.commit()

    repo = WarehouseProductivityRepository(db_url)
    result = repo.list_rows({})
    assert result["row_count"] == 4
    assert result["total_quantity"] == 15
    assert {row["task_type"] for row in result["rows"]} == {
        "fbs",
        "wb_fbo",
        "wb_fbo_new",
        "vseinstrumenti",
    }
    assert all(row["date"] == "2026-10-01" for row in result["rows"])
    assert all(row["employee"] == "Анна Упаковщик" for row in result["rows"])

    fbs = repo.list_rows({"task_type": "fbs"})
    assert fbs["total_quantity"] == 2
    assert "SUP-FBS" in fbs["rows"][0]["task_data"]

    by_task = repo.list_rows({"task_query": "WB-77"})
    assert by_task["total_quantity"] == 6
    assert by_task["rows"][0]["task_type"] == "wb_fbo"

    by_quantity = repo.list_rows({"min_quantity": "4"})
    assert by_quantity["total_quantity"] == 10
    assert by_quantity["row_count"] == 2

    assert repo.list_rows({"date_from": "2026-10-02"})["rows"] == []

    replacement = users.create_user(
        login="replacement",
        password="secret",
        display_name="Мария Упаковщик",
    )
    for row in result["rows"]:
        changed = repo.reassign_row(
            event_date=row["date"],
            task_type=row["task_type"],
            task_id=row["task_id"],
            from_user_id=worker.id,
            to_user_id=replacement.id,
        )
        assert changed["updated_count"] == (2 if row["task_type"] == "fbs" else 1)
        assert changed["employee"] == "Мария Упаковщик"

    reassigned = repo.list_rows({"user_id": str(replacement.id)})
    assert reassigned["row_count"] == 4
    assert reassigned["total_quantity"] == 15
    assert all(row["employee"] == "Мария Упаковщик" for row in reassigned["rows"])
    assert repo.list_rows({"user_id": str(worker.id)})["rows"] == []
    with Session(users.engine) as session:
        saved_box = session.get(WbFboSheetBox, box_id)
        assert saved_box is not None
        assert saved_box.assigned_by_user_id == replacement.id


def test_vseinstrumenti_records_worker_and_clears_on_reopen(db_url, tmp_path):
    users, repo = _init(db_url, tmp_path)
    worker = users.create_user(login="worker", password="secret", display_name="Работник")
    job = repo.create_job(
        platform="vseinstrumenti",
        order_number="VI-1",
        transfer_number="",
        supplier="",
        delivery_date="",
        purchase_status="Закуплено",
        source_filename="vi.xlsx",
        created_by_user_id=None,
        packer_user_ids=[worker.id],
        lines=[
            {
                "sku": "SKU",
                "product_name": "Товар",
                "excel_barcode": "123",
                "quantity": 2,
            }
        ],
    )
    line_id = job.lines[0].id
    done = repo.set_line_status(job.id, line_id, LINE_DONE, user_id=worker.id)
    line = done.lines[0]
    assert line.done_by_user_id == worker.id
    assert line.done_at_ts is not None

    pending = repo.set_line_status(job.id, line_id, LINE_PENDING, user_id=worker.id)
    line = pending.lines[0]
    assert line.done_by_user_id is None
    assert line.done_at_ts is None
