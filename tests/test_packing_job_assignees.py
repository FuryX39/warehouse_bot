from sqlalchemy.orm import Session

from app.fbs_packing_repository import (
    FbsPackingJob,
    FbsPackingJobAssignee,
    FbsPackingRepository,
)
from app.other_marketplace_repository import (
    OtherMarketplaceAssignee,
    OtherMarketplaceJob,
    OtherMarketplaceRepository,
)
from app.warehouse_users_repository import WarehouseUsersRepository
from app.wb_fbo_packing_repository import (
    WbFboPackingJob,
    WbFboPackingJobAssignee,
    WbFboPackingRepository,
)
from app.wb_fbo_sheet_repository import (
    WbFboSheetJob,
    WbFboSheetJobAssignee,
    WbFboSheetRepository,
)
from app.web.warehouse_assignment_helpers import requested_packer_ids


def test_existing_packing_jobs_can_change_assignees(db_url, tmp_path):
    users = WarehouseUsersRepository(db_url)
    users.init_schema()
    first = users.create_user(login="first", password="secret", display_name="Первый")
    second = users.create_user(login="second", password="secret", display_name="Второй")
    third = users.create_user(login="third", password="secret", display_name="Третий")

    fbs = FbsPackingRepository(db_url, files_data_dir=tmp_path / "fbs")
    wb_fbo = WbFboPackingRepository(db_url, files_data_dir=tmp_path / "fbo")
    wb_new = WbFboSheetRepository(db_url, files_data_dir=tmp_path / "new")
    other = OtherMarketplaceRepository(db_url)
    for repo in (fbs, wb_fbo, wb_new, other):
        repo.init_schema()

    with Session(users.engine) as session:
        fbs_job = FbsPackingJob()
        wb_fbo_job = WbFboPackingJob()
        wb_new_job = WbFboSheetJob()
        other_job = OtherMarketplaceJob()
        session.add_all([fbs_job, wb_fbo_job, wb_new_job, other_job])
        session.flush()
        job_ids = {
            "fbs": int(fbs_job.id),
            "wb_fbo": int(wb_fbo_job.id),
            "wb_new": int(wb_new_job.id),
            "other": int(other_job.id),
        }
        session.add_all(
            [
                FbsPackingJobAssignee(job_id=job_ids["fbs"], user_id=first.id),
                WbFboPackingJobAssignee(job_id=job_ids["wb_fbo"], user_id=first.id),
                WbFboSheetJobAssignee(job_id=job_ids["wb_new"], user_id=first.id),
                OtherMarketplaceAssignee(job_id=job_ids["other"], user_id=first.id),
            ]
        )
        session.commit()

    fbs_row = fbs.set_assignees(job_ids["fbs"], [second.id, third.id, second.id])
    fbo_row = wb_fbo.set_assignees(job_ids["wb_fbo"], [second.id, third.id])
    new_row = wb_new.set_assignees(job_ids["wb_new"], [second.id, third.id])
    other_row = other.set_assignees(job_ids["other"], [second.id, third.id])

    assert set(fbs_row.packer_user_ids) == {second.id, third.id}
    assert set(fbo_row.packer_user_ids) == {second.id, third.id}
    assert set(new_row.packer_user_ids) == {second.id, third.id}
    assert set(other_row.packer_user_ids) == {second.id, third.id}
    assert not fbs.user_can_pack(job_ids["fbs"], first.id)
    assert wb_fbo.user_can_pack(job_ids["wb_fbo"], third.id)
    assert wb_new.user_can_pack(job_ids["wb_new"], third.id)


def test_all_assignment_uses_every_active_employee(db_url):
    users = WarehouseUsersRepository(db_url)
    users.init_schema()
    first = users.create_user(login="active-1", password="secret", display_name="Первый")
    second = users.create_user(login="active-2", password="secret", display_name="Второй")
    users.create_user(
        login="inactive",
        password="secret",
        display_name="Неактивный",
        is_active=False,
    )

    assert set(requested_packer_ids({"all": True}, users)) == {first.id, second.id}
