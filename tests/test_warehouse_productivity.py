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
from app.yandex_fbo_repository import (
    YandexFboCargo,
    YandexFboCargoItem,
    YandexFboJob,
    YandexFboProduct,
    YandexFboRepository,
)


def _init(db_url, tmp_path):
    users = WarehouseUsersRepository(db_url)
    users.init_schema()
    FbsPackingRepository(db_url, files_data_dir=tmp_path / "fbs").init_schema()
    WbFboPackingRepository(db_url, files_data_dir=tmp_path / "fbo").init_schema()
    WbFboSheetRepository(db_url, files_data_dir=tmp_path / "sheet").init_schema()
    YandexFboRepository(db_url, files_data_dir=tmp_path / "ym").init_schema()
    other = OtherMarketplaceRepository(db_url)
    other.init_schema()
    WarehouseProductivityRepository(db_url).init_schema()
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
        fbs_job_id = int(fbs_job.id)
        fbo_job_id = int(fbo_job.id)
        sheet_job_id = int(sheet_job.id)
        other_job_id = int(other_job.id)

        ym_job = YandexFboJob(
            request_id=10180422,
            marketplace_request_id="33388649",
            warehouse_request_id="0001142103",
            parent_request_id=10180419,
            vrc_label="ВРЦ-10180419",
            warehouse_name="МО Софьино",
            transit_warehouse="ПВЗ Одоевского",
            transit_at="2026-10-07T09:00:00+07:00",
            accept_at="2026-10-16T06:00:00Z",
            supply_status="ACCEPTED_AT_WAREHOUSE",
            cargo_labels_stored_name="",
            status="done",
            created_by_user_id=None,
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(ym_job)
        session.flush()
        ym_product = YandexFboProduct(
            job_id=ym_job.id,
            seq=1,
            sku="SS694",
            catalog_product_id=None,
            product_name="Shine Systems FastQuartz",
            planned_qty=40,
            scan_keys_json='["SS694"]',
        )
        session.add(ym_product)
        session.flush()
        ym_cargo = YandexFboCargo(
            job_id=ym_job.id,
            seq=1,
            page_index=0,
            cargo_code="B02540C0DF00035BC1CE",
        )
        session.add(ym_cargo)
        session.flush()
        session.add(
            YandexFboCargoItem(
                job_id=ym_job.id,
                cargo_id=ym_cargo.id,
                product_id=ym_product.id,
                quantity=5,
                assigned_at_ts=event_ts,
                assigned_by_user_id=worker.id,
            )
        )
        ym_job_id = int(ym_job.id)
        session.commit()

    repo = WarehouseProductivityRepository(db_url)
    result = repo.list_rows({})
    assert result["row_count"] == 5
    assert result["total_quantity"] == 20
    assert {row["task_type"] for row in result["rows"]} == {
        "fbs",
        "wb_fbo",
        "wb_fbo_new",
        "ym_fbo",
        "vseinstrumenti",
    }
    assert all(row["date"] == "2026-10-01" for row in result["rows"])
    assert all(row["employee"] == "Анна Упаковщик" for row in result["rows"])
    assert all(row["counted"] is True for row in result["rows"])
    assert all(row["pay"] == "" for row in result["rows"])
    assert result["total_pay"] == ""

    month = repo.list_user_month(user_id=worker.id, year=2026, month=10)
    assert month["month"] == "2026-10"
    assert month["total_quantity"] == 20
    assert month["total_pay"] == ""
    assert month["days"][0]["pay"] == ""
    assert len(month["days"]) == 1
    assert month["days"][0]["display_date"] == "01.10.2026"
    assert month["days"][0]["quantity"] == 20
    assert {task["task_type"] for task in month["days"][0]["tasks"]} == {
        "fbs",
        "wb_fbo",
        "wb_fbo_new",
        "ym_fbo",
        "vseinstrumenti",
    }

    fbs = repo.list_rows({"task_type": "fbs"})
    assert fbs["total_quantity"] == 2
    assert "SUP-FBS" in fbs["rows"][0]["task_data"]
    fbs_details = repo.list_details(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=fbs_job_id,
        user_id=worker.id,
    )
    assert fbs_details["row_count"] == 2
    assert fbs_details["quantity"] == 2
    assert {row["reference"] for row in fbs_details["details"]} == {"O-1", "O-2"}
    assert all(
        row["completed_at"] == "01.10.2026 12:00:00"
        for row in fbs_details["details"]
    )

    fbo_details = repo.list_details(
        event_date="2026-10-01",
        task_type="wb_fbo",
        task_id=fbo_job_id,
        user_id=worker.id,
    )
    assert fbo_details["quantity"] == 6
    assert fbo_details["details"][0]["reference"] == "1"
    assert fbo_details["details"][0]["sku"] == "FBO-1"

    sheet_details = repo.list_details(
        event_date="2026-10-01",
        task_type="wb_fbo_new",
        task_id=sheet_job_id,
        user_id=worker.id,
    )
    assert sheet_details["quantity"] == 4
    assert sheet_details["details"][0]["reference"] == "BOX-1"
    assert sheet_details["details"][0]["sku"] == "4600"

    vi_details = repo.list_details(
        event_date="2026-10-01",
        task_type="vseinstrumenti",
        task_id=other_job_id,
        user_id=worker.id,
    )
    assert vi_details["quantity"] == 3
    assert vi_details["details"][0]["reference"] == "VI-42"
    assert vi_details["details"][0]["product_name"] == "Инструмент"

    ym_details = repo.list_details(
        event_date="2026-10-01",
        task_type="ym_fbo",
        task_id=ym_job_id,
        user_id=worker.id,
    )
    assert ym_details["quantity"] == 5
    assert ym_details["details"][0]["reference"] == "B02540C0DF00035BC1CE"
    assert ym_details["details"][0]["sku"] == "SS694"

    by_task = repo.list_rows({"task_query": "WB-77"})
    assert by_task["total_quantity"] == 6
    assert by_task["rows"][0]["task_type"] == "wb_fbo"

    by_quantity = repo.list_rows({"min_quantity": "4"})
    assert by_quantity["total_quantity"] == 15
    assert by_quantity["row_count"] == 3

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
    assert reassigned["row_count"] == 5
    assert reassigned["total_quantity"] == 20
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


def test_reschedule_row_keeps_time_of_day(db_url, tmp_path):
    users, _ = _init(db_url, tmp_path)
    worker = users.create_user(login="packer", password="secret", display_name="Анна")
    tz = ZoneInfo("Asia/Novosibirsk")
    noon = int(datetime(2026, 10, 1, 12, 0, tzinfo=tz).timestamp())
    half = int(datetime(2026, 10, 1, 12, 30, tzinfo=tz).timestamp())
    with Session(users.engine) as session:
        job = FbsPackingJob(
            marketplace="wildberries",
            order_substatus="STARTED",
            build_list=True,
            require_cis=False,
            supply_id="SUP-DATE",
            transfer_number="TR-D",
            status="done",
            created_by_user_id=None,
            sheet_url="",
            sheet_title="",
            merged_label_stored_name="",
            warnings_json="[]",
            created_at_ts=noon,
            updated_at_ts=noon,
        )
        session.add(job)
        session.flush()
        session.add(
            FbsPackingLine(
                job_id=job.id,
                seq=1,
                sku="SKU-1",
                product_id=None,
                product_name="Товар",
                order_id="O-1",
                box_id=None,
                place_index=1,
                place_total=1,
                scan_keys_json="[]",
                label_stored_name="",
                status="done",
                printed_at_ts=noon,
                done_at_ts=noon,
                done_by_user_id=worker.id,
                cis_raw="",
                cis_key="",
                cis_gtin="",
            )
        )
        session.add(
            FbsPackingLine(
                job_id=job.id,
                seq=2,
                sku="SKU-2",
                product_id=None,
                product_name="Товар",
                order_id="O-2",
                box_id=None,
                place_index=1,
                place_total=1,
                scan_keys_json="[]",
                label_stored_name="",
                status="done",
                printed_at_ts=half,
                done_at_ts=half,
                done_by_user_id=worker.id,
                cis_raw="",
                cis_key="",
                cis_gtin="",
            )
        )
        job_id = int(job.id)
        session.commit()

    repo = WarehouseProductivityRepository(db_url)
    try:
        repo.reschedule_row(
            event_date="2026-10-01",
            task_type="fbs",
            task_id=job_id,
            user_id=worker.id,
            to_date="2026-10-01",
        )
    except ValueError as exc:
        assert "другую дату" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    changed = repo.reschedule_row(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=job_id,
        user_id=worker.id,
        to_date="2026-10-03",
    )
    assert changed["updated_count"] == 2
    assert changed["date"] == "2026-10-03"
    assert repo.list_rows({"date_from": "2026-10-01", "date_to": "2026-10-01"})["rows"] == []

    moved = repo.list_rows({"date_from": "2026-10-03", "date_to": "2026-10-03"})
    assert moved["row_count"] == 1
    assert moved["rows"][0]["date"] == "2026-10-03"
    assert moved["rows"][0]["quantity"] == 2

    details = repo.list_details(
        event_date="2026-10-03",
        task_type="fbs",
        task_id=job_id,
        user_id=worker.id,
    )
    assert {row["completed_at"] for row in details["details"]} == {
        "03.10.2026 12:30:00",
        "03.10.2026 12:00:00",
    }


def _add_fbs_job(session, worker_id: int, event_ts: int, supply_id: str) -> int:
    job = FbsPackingJob(
        marketplace="wildberries",
        order_substatus="STARTED",
        build_list=True,
        require_cis=False,
        supply_id=supply_id,
        transfer_number="TR-P",
        status="done",
        created_by_user_id=None,
        sheet_url="",
        sheet_title="",
        merged_label_stored_name="",
        warnings_json="[]",
        created_at_ts=event_ts,
        updated_at_ts=event_ts,
    )
    session.add(job)
    session.flush()
    session.add(
        FbsPackingLine(
            job_id=job.id,
            seq=1,
            sku="SKU-1",
            product_id=None,
            product_name="Товар",
            order_id="O-1",
            box_id=None,
            place_index=1,
            place_total=1,
            scan_keys_json="[]",
            label_stored_name="",
            status="done",
            printed_at_ts=event_ts,
            done_at_ts=event_ts,
            done_by_user_id=worker_id,
            cis_raw="",
            cis_key="",
            cis_gtin="",
        )
    )
    session.add(
        FbsPackingLine(
            job_id=job.id,
            seq=2,
            sku="SKU-2",
            product_id=None,
            product_name="Товар",
            order_id="O-2",
            box_id=None,
            place_index=1,
            place_total=1,
            scan_keys_json="[]",
            label_stored_name="",
            status="done",
            printed_at_ts=event_ts,
            done_at_ts=event_ts,
            done_by_user_id=worker_id,
            cis_raw="",
            cis_key="",
            cis_gtin="",
        )
    )
    return int(job.id)


def test_productivity_rates_pay_and_exclusion(db_url, tmp_path):
    users, _ = _init(db_url, tmp_path)
    worker = users.create_user(login="packer", password="secret", display_name="Анна")
    other = users.create_user(login="other", password="secret", display_name="Мария")
    event_ts = int(
        datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")).timestamp()
    )
    with Session(users.engine) as session:
        fbs_job_id = _add_fbs_job(session, worker.id, event_ts, "SUP-PAY")
        sheet_job = WbFboSheetJob(
            supply_id="SHEET-PAY",
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
        sheet_job_id = int(sheet_job.id)
        session.commit()

    repo = WarehouseProductivityRepository(db_url)
    empty = repo.list_rows({})
    assert empty["total_pay"] == ""
    assert all(row["pay"] == "" for row in empty["rows"])

    saved = repo.save_rates(
        [
            {"id": "fbs", "rate": "10,5"},
            {"id": "wb_fbo", "rate": "3"},
            {"id": "ym_fbo", "rate": ""},
            {"id": "ozon_fbo", "rate": "1"},
            {"id": "vseinstrumenti", "rate": "2"},
        ]
    )
    assert {item["id"]: item["rate"] for item in saved["rates"]} == {
        "fbs": "10.50",
        "wb_fbo": "3",
        "ym_fbo": "",
        "ozon_fbo": "1",
        "vseinstrumenti": "2",
        "rework": "",
    }

    paid = repo.list_rows({})
    by_type = {row["task_type"]: row for row in paid["rows"]}
    assert by_type["fbs"]["pay"] == "21"
    assert by_type["wb_fbo_new"]["pay"] == "12"
    assert paid["total_quantity"] == 6
    assert paid["total_pay"] == "33"

    month = repo.list_user_month(user_id=worker.id, year=2026, month=10)
    assert month["total_pay"] == "33"
    assert month["days"][0]["pay"] == "33"
    assert {task["task_type"]: task["pay"] for task in month["days"][0]["tasks"]} == {
        "fbs": "21",
        "wb_fbo_new": "12",
    }

    excluded = repo.set_row_counted(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=fbs_job_id,
        user_id=worker.id,
        counted=False,
    )
    assert excluded["counted"] is False
    after = repo.list_rows({})
    after_by_type = {row["task_type"]: row for row in after["rows"]}
    assert after["row_count"] == 2
    assert after["total_quantity"] == 4
    assert after["total_pay"] == "12"
    assert after_by_type["fbs"]["counted"] is False
    assert after_by_type["fbs"]["pay"] == ""
    assert after_by_type["fbs"]["quantity"] == 2
    assert after_by_type["wb_fbo_new"]["counted"] is True

    packer = repo.list_user_month(user_id=worker.id, year=2026, month=10)
    assert packer["total_quantity"] == 4
    assert packer["total_pay"] == "12"
    assert [task["task_type"] for task in packer["days"][0]["tasks"]] == ["wb_fbo_new"]

    repo.reassign_row(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=fbs_job_id,
        from_user_id=worker.id,
        to_user_id=other.id,
    )
    moved = repo.list_rows({"user_id": str(other.id)})
    assert moved["row_count"] == 1
    assert moved["rows"][0]["counted"] is False
    assert moved["total_quantity"] == 0
    assert moved["total_pay"] == ""
    assert repo.list_user_month(user_id=other.id, year=2026, month=10)["days"] == []

    repo.reschedule_row(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=fbs_job_id,
        user_id=other.id,
        to_date="2026-10-05",
    )
    shifted = repo.list_rows({"date_from": "2026-10-05", "date_to": "2026-10-05"})
    assert shifted["rows"][0]["counted"] is False
    assert shifted["total_quantity"] == 0

    restored = repo.set_row_counted(
        event_date="2026-10-05",
        task_type="fbs",
        task_id=fbs_job_id,
        user_id=other.id,
        counted=True,
    )
    assert restored["counted"] is True
    enabled = repo.list_rows({"user_id": str(other.id)})
    assert enabled["total_quantity"] == 2
    assert enabled["total_pay"] == "21"
    assert enabled["rows"][0]["counted"] is True

    try:
        repo.save_rates([{"id": "fbs", "rate": "abc"}])
    except ValueError as exc:
        assert "числом" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_productivity_manual_rework_rows(db_url, tmp_path):
    users, _ = _init(db_url, tmp_path)
    worker = users.create_user(login="packer", password="secret", display_name="Анна")
    other = users.create_user(login="other", password="secret", display_name="Мария")
    repo = WarehouseProductivityRepository(db_url)
    created = repo.add_manual_row(
        event_date="2026-10-06",
        user_id=worker.id,
        quantity=7,
        comment="Переклейка этикеток после отмены",
    )
    assert created["task_type"] == "rework"
    assert created["task_data"] == "Переклейка этикеток после отмены"

    repo.save_rates([{"id": "rework", "rate": "5"}])
    rows = repo.list_rows({"task_type": "rework"})
    assert rows["row_count"] == 1
    assert rows["total_quantity"] == 7
    assert rows["total_pay"] == "35"
    assert rows["rows"][0]["task_type_name"] == "Доработка"
    assert rows["rows"][0]["task_data"] == "Переклейка этикеток после отмены"
    assert rows["rows"][0]["pay"] == "35"

    details = repo.list_details(
        event_date="2026-10-06",
        task_type="rework",
        task_id=created["task_id"],
        user_id=worker.id,
    )
    assert details["quantity"] == 7
    assert details["details"][0]["product_name"] == "Переклейка этикеток после отмены"

    month = repo.list_user_month(user_id=worker.id, year=2026, month=10)
    assert month["total_quantity"] == 7
    assert month["days"][0]["tasks"][0]["task_type"] == "rework"

    repo.reassign_row(
        event_date="2026-10-06",
        task_type="rework",
        task_id=created["task_id"],
        from_user_id=worker.id,
        to_user_id=other.id,
    )
    repo.reschedule_row(
        event_date="2026-10-06",
        task_type="rework",
        task_id=created["task_id"],
        user_id=other.id,
        to_date="2026-10-08",
    )
    moved = repo.list_rows({"user_id": str(other.id)})
    assert moved["rows"][0]["date"] == "2026-10-08"
    assert moved["total_quantity"] == 7
    assert repo.list_rows({"user_id": str(worker.id)})["rows"] == []

    repo.set_row_counted(
        event_date="2026-10-08",
        task_type="rework",
        task_id=created["task_id"],
        user_id=other.id,
        counted=False,
    )
    hidden = repo.list_rows({"user_id": str(other.id)})
    assert hidden["total_quantity"] == 0
    assert hidden["total_pay"] == ""
    assert hidden["rows"][0]["counted"] is False
    assert repo.list_user_month(user_id=other.id, year=2026, month=10)["days"] == []

    try:
        repo.add_manual_row(
            event_date="2026-10-06",
            user_id=worker.id,
            quantity=0,
            comment="x",
        )
    except ValueError as exc:
        assert "больше нуля" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_productivity_share_splits_only_source_work(db_url, tmp_path):
    users, _ = _init(db_url, tmp_path)
    anna = users.create_user(login="anna", password="secret", display_name="Анна")
    boris = users.create_user(login="boris", password="secret", display_name="Борис")
    dmitry = users.create_user(login="dmitry", password="secret", display_name="Дмитрий")
    event_ts = int(
        datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk")).timestamp()
    )
    with Session(users.engine) as session:
        job = FbsPackingJob(
            marketplace="wildberries",
            order_substatus="STARTED",
            build_list=True,
            require_cis=False,
            supply_id="SUP-SHARE",
            transfer_number="",
            status="done",
            created_by_user_id=None,
            sheet_url="",
            sheet_title="",
            merged_label_stored_name="",
            warnings_json="[]",
            created_at_ts=event_ts,
            updated_at_ts=event_ts,
        )
        session.add(job)
        session.flush()
        job_id = int(job.id)
        for seq in range(1, 5):
            session.add(
                FbsPackingLine(
                    job_id=job.id,
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
                    done_by_user_id=anna.id,
                    cis_raw="",
                    cis_key="",
                    cis_gtin="",
                )
            )
        for seq in (5, 6):
            session.add(
                FbsPackingLine(
                    job_id=job.id,
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
                    done_by_user_id=dmitry.id,
                    cis_raw="",
                    cis_key="",
                    cis_gtin="",
                )
            )
        session.commit()

    repo = WarehouseProductivityRepository(db_url)
    repo.init_schema()
    saved = repo.set_shared_packers(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=job_id,
        source_user_id=anna.id,
        user_ids=[boris.id],
        created_by_user_id=anna.id,
    )
    assert saved["packer_user_ids"] == [boris.id]

    anna_rows = repo.list_rows({"user_id": str(anna.id), "date_from": "2026-10-01", "date_to": "2026-10-01"})
    boris_rows = repo.list_rows({"user_id": str(boris.id), "date_from": "2026-10-01", "date_to": "2026-10-01"})
    dmitry_rows = repo.list_rows({"user_id": str(dmitry.id), "date_from": "2026-10-01", "date_to": "2026-10-01"})
    assert anna_rows["rows"][0]["quantity"] == 2
    assert anna_rows["rows"][0]["packer_user_ids"] == [boris.id]
    assert boris_rows["rows"][0]["quantity"] == 2
    assert boris_rows["rows"][0]["has_own_work"] is False
    assert dmitry_rows["rows"][0]["quantity"] == 2
    assert dmitry_rows["total_quantity"] == 2

    month = repo.list_user_month(user_id=anna.id, year=2026, month=10)
    assert month["total_quantity"] == 2
    assert month["days"][0]["tasks"][0]["packer_user_ids"] == [boris.id]

    try:
        repo.set_shared_packers(
            event_date="2026-10-01",
            task_type="fbs",
            task_id=job_id,
            source_user_id=boris.id,
            user_ids=[anna.id],
            created_by_user_id=boris.id,
        )
    except ValueError as exc:
        assert "собственной выработки" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    repo.set_shared_packers(
        event_date="2026-10-01",
        task_type="fbs",
        task_id=job_id,
        source_user_id=anna.id,
        user_ids=[],
        created_by_user_id=anna.id,
    )
    restored = repo.list_rows({"user_id": str(anna.id), "date_from": "2026-10-01", "date_to": "2026-10-01"})
    assert restored["rows"][0]["quantity"] == 4
    assert repo.list_rows({"user_id": str(boris.id), "date_from": "2026-10-01", "date_to": "2026-10-01"})["rows"] == []
