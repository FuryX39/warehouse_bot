"""Тесты PDF-вложений задач упаковщикам."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.warehouse_task_files import WarehouseTaskFileStorage
from app.warehouse_tasks_repository import WarehouseTasksRepository
from app.warehouse_users_repository import WarehouseUsersRepository

_SAMPLE_PDF = b"%PDF-1.4\n% test attachment\n"


def _make_tasks_repo(tmp_path) -> tuple[WarehouseTasksRepository, WarehouseUsersRepository, int, int]:
    db_path = tmp_path / "tasks.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    files_dir = tmp_path / "task_files"

    users_repo = WarehouseUsersRepository(db_url)
    users_repo.init_schema()
    packer = users_repo.create_user(login="packer", password="secret", display_name="Упаковщик")
    manager = users_repo.create_user(login="manager", password="secret", display_name="Менеджер")

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
        task_files_data_dir=files_dir,
    )
    tasks_repo.init_schema()
    task_type = tasks_repo.create_task_type({"name": "Упаковка", "comment": "Шаблон"})
    task = tasks_repo.create_task(
        {
            "task_type_id": task_type["id"],
            "description": "Описание для упаковщика",
            "assignee_ids": [packer.id],
            "start_date": "2026-07-20",
            "end_date": "2026-07-21",
        },
        created_by_user_id=manager.id,
    )
    return tasks_repo, users_repo, int(task.id), int(packer.id)


def test_task_attachment_upload_list_download_delete(tmp_path) -> None:
    tasks_repo, _users_repo, task_id, _packer_id = _make_tasks_repo(tmp_path)

    attachment = tasks_repo.add_attachment(
        task_id,
        kind="a4",
        original_filename="sheet.pdf",
        content=_SAMPLE_PDF,
    )
    assert attachment.kind == "a4"
    assert attachment.original_filename == "sheet.pdf"
    assert attachment.file_size == len(_SAMPLE_PDF)

    task = tasks_repo.get_task(task_id)
    assert task is not None
    assert len(task.attachments) == 1
    assert task.attachments[0].id == attachment.id

    found = tasks_repo.get_attachment_file(task_id, attachment.id)
    assert found is not None
    row, path = found
    assert row.original_filename == "sheet.pdf"
    assert path.read_bytes() == _SAMPLE_PDF

    payload = tasks_repo.task_to_dict(task)
    assert payload["attachments"][0]["filename"] == "sheet.pdf"
    assert payload["attachments"][0]["kind"] == "a4"

    assert tasks_repo.delete_attachment(task_id, attachment.id) is True
    assert tasks_repo.get_attachment_file(task_id, attachment.id) is None
    task_after = tasks_repo.get_task(task_id)
    assert task_after is not None
    assert task_after.attachments == []


def test_task_attachment_rejects_invalid_kind(tmp_path) -> None:
    tasks_repo, _users_repo, task_id, _packer_id = _make_tasks_repo(tmp_path)
    with pytest.raises(ValueError, match="a4 или label"):
        tasks_repo.add_attachment(
            task_id,
            kind="doc",
            original_filename="bad.pdf",
            content=_SAMPLE_PDF,
        )


def test_task_file_storage_rejects_non_pdf(tmp_path) -> None:
    storage = WarehouseTaskFileStorage(tmp_path / "files")
    with pytest.raises(ValueError, match="PDF"):
        storage.store_pdf(content=b"not-a-pdf", original_filename="x.pdf")


def test_task_description_saved_in_task_dict(tmp_path) -> None:
    tasks_repo, _users_repo, task_id, _packer_id = _make_tasks_repo(tmp_path)
    task = tasks_repo.get_task(task_id)
    assert task is not None
    payload = tasks_repo.task_to_dict(task)
    assert payload["description"] == "Описание для упаковщика"

    updated = tasks_repo.update_task(
        task_id,
        {
            "task_type_id": task.task_type_id,
            "description": "Новое описание",
            "assignee_ids": [a.user_id for a in task.assignees],
        },
    )
    assert updated is not None
    assert tasks_repo.task_to_dict(updated)["description"] == "Новое описание"


def test_task_date_validation_message(tmp_path) -> None:
    tasks_repo, _users_repo, task_id, _packer_id = _make_tasks_repo(tmp_path)
    task = tasks_repo.get_task(task_id)
    assert task is not None
    with pytest.raises(ValueError, match="Дата отгрузки не может быть раньше даты сборки"):
        tasks_repo.update_task(
            task_id,
            {
                "task_type_id": task.task_type_id,
                "start_date": "2026-07-25",
                "end_date": "2026-07-20",
                "assignee_ids": [a.user_id for a in task.assignees],
            },
        )
