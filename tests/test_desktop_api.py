"""Отдельный процесс desktop API: логин сотрудника и /api/v1."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.warehouse_users_repository import WarehouseUsersRepository
from app.web.desktop_api import create_desktop_api_app


def _settings(tmp_path) -> Settings:
    db_url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    return Settings(
        telegram_bot_token="t",
        db_url=db_url,
        movement_db_url=db_url,
        web_dashboard_secret="cookie-secret",
        warehouse_task_files_data_dir=str(tmp_path / "task_files"),
    )


def test_desktop_api_health_login_and_packing_my(tmp_path) -> None:
    settings = _settings(tmp_path)
    users = WarehouseUsersRepository(settings.db_url)
    users.init_schema()
    users.create_user(login="packer", password="secret", display_name="Упаковщик")

    client = TestClient(create_desktop_api_app(settings))
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    denied = client.get("/api/v1/fbs-packing/my")
    assert denied.status_code == 401

    bad = client.post("/api/v1/login", json={"login": "packer", "password": "wrong"})
    assert bad.status_code == 401

    logged = client.post("/api/v1/login", json={"login": "packer", "password": "secret"})
    assert logged.status_code == 200, logged.text
    assert logged.json()["user"]["login"] == "packer"

    mine = client.get("/api/v1/fbs-packing/my")
    assert mine.status_code == 200, mine.text
    assert mine.json()["jobs"] == []

    schema = client.get("/api/v1/tasks/schema")
    assert schema.status_code == 200, schema.text
    assert schema.json()["base_paths"] == ["/api/v1/tasks"]

    assert client.get("/api/warehouse/fbs-packing/jobs").status_code == 404
    assert client.get("/api/warehouse/tasks/schema").status_code == 404
