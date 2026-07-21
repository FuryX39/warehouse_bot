"""Тесты списка «мои задачи» для упаковщиков."""

from __future__ import annotations

from types import SimpleNamespace

from app.warehouse_tasks_repository import WarehouseTasksRepository
from app.warehouse_users_repository import WarehouseUsersRepository


def _make_tasks_repo(tmp_path) -> tuple[WarehouseTasksRepository, int, int]:
    db_path = tmp_path / "tasks.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    users_repo = WarehouseUsersRepository(db_url)
    users_repo.init_schema()
    packer_a = users_repo.create_user(login="packer_a", password="secret", display_name="А")
    packer_b = users_repo.create_user(login="packer_b", password="secret", display_name="Б")
    manager = users_repo.create_user(login="manager", password="secret", display_name="М")

    stub = SimpleNamespace(
        get_transfer=lambda _id: None,
        list_transfers=lambda *_a, **_k: [],
        get_receipt=lambda _id: None,
        list_receipts=lambda *_a, **_k: [],
        get_writeoff=lambda _id: None,
        list_writeoffs=lambda *_a, **_k: [],
        get_counterparty=lambda _id: None,
    )
    tasks_repo = WarehouseTasksRepository(
        db_url,
        users_repo,
        stub,
        stub,
        stub,
        stub,
        stub,
        task_files_data_dir=tmp_path / "files",
    )
    tasks_repo.init_schema()
    task_type = tasks_repo.create_task_type({"name": "Сборка"})
    tasks_repo.create_task(
        {
            "task_type_id": task_type["id"],
            "assignee_ids": [packer_a.id],
            "start_date": "2026-07-22",
            "end_date": "2026-07-24",
        },
        created_by_user_id=manager.id,
    )
    tasks_repo.create_task(
        {
            "task_type_id": task_type["id"],
            "assignee_ids": [packer_b.id],
            "start_date": "2026-07-20",
            "end_date": "2026-07-21",
        },
        created_by_user_id=manager.id,
    )
    tasks_repo.create_task(
        {
            "task_type_id": task_type["id"],
            "assignee_ids": [packer_a.id, packer_b.id],
            "start_date": "2026-07-21",
            "end_date": "2026-07-23",
        },
        created_by_user_id=manager.id,
    )
    return tasks_repo, int(packer_a.id), int(packer_b.id)


def test_list_my_tasks_filters_by_assignee_and_sorts_by_dates(tmp_path) -> None:
    tasks_repo, packer_a_id, packer_b_id = _make_tasks_repo(tmp_path)

    rows_a = tasks_repo.list_my_tasks(packer_a_id)
    assert len(rows_a) == 2
    assert [r.start_date_ts for r in rows_a if r.start_date_ts] == sorted(
        r.start_date_ts for r in rows_a if r.start_date_ts
    )

    rows_b = tasks_repo.list_my_tasks(packer_b_id)
    assert len(rows_b) == 2
    starts = [tasks_repo.task_to_dict(r)["start_date"] for r in rows_b]
    assert starts == sorted(starts)

    all_tasks = tasks_repo.list_tasks({}, limit=100, offset=0)
    assert len(all_tasks) == 3
    assert len(rows_a) + len(rows_b) == 4  # shared task counted twice
