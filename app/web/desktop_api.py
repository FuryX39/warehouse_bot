"""Отдельный HTTP-процесс для десктопа: /api/v1 (задачи и FBS-упаковка).

Веб-панель (run_web.py) отдаёт браузер и /api/warehouse/*.
Этот модуль — только обращения программ упаковщиков/планирования.
Общая БД и каталог файлов заданий с вебом.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app.catalog_repository import CatalogRepository
from app.config import Settings, resolve_warehouse_admin_credentials
from app.crm_repository import CrmRepository
from app.fbs_packing_repository import FbsPackingRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.warehouse_schedule_repository import WarehouseScheduleRepository
from app.warehouse_task_summary_repository import WarehouseTaskSummaryRepository
from app.warehouse_tasks_repository import WarehouseTasksRepository
from app.warehouse_transfers_repository import WarehouseTransfersRepository
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository
from app.web.warehouse_fbs_packing_routes import register_warehouse_fbs_packing_routes
from app.web.warehouse_tasks_api_auth import make_require_tasks_access
from app.web.warehouse_tasks_routes import register_warehouse_tasks_routes

_SESSION_COOKIE = "warehouse_api_session"
_SESSION_KEY_PREFIX = "warehouse_api_session_signing_v1:"
_WH_SESSION_USER_KEY = "wh_user_id"


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class LoginBody(BaseModel):
    login: str = Field(default="")
    password: str = Field(default="")


def _session_signing_key(secret: str) -> str:
    return hashlib.sha256((_SESSION_KEY_PREFIX + secret).encode("utf-8")).hexdigest()


def create_desktop_api_app(settings: Settings) -> FastAPI:
    signing_secret = (
        (settings.web_dashboard_secret or "").strip()
        or (settings.warehouse_admin_password or "").strip()
    )
    if not signing_secret:
        raise RuntimeError(
            "Для API нужен WEB_DASHBOARD_SECRET или WAREHOUSE_ADMIN_PASSWORD "
            "(подпись cookie сессии десктопа)."
        )

    app = FastAPI(
        title="Warehouse desktop API",
        version="1.0",
        default_response_class=Utf8JSONResponse,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_signing_key(signing_secret),
        session_cookie=_SESSION_COOKIE,
        max_age=86400 * 14,
        same_site="lax",
        https_only=False,
    )

    warehouse_users_repo = WarehouseUsersRepository(settings.db_url)
    warehouse_users_repo.init_schema()

    warehouse_schedule_repo = WarehouseScheduleRepository(settings.db_url)
    warehouse_schedule_repo.init_schema()

    warehouse_task_summary_repo = WarehouseTaskSummaryRepository(settings.db_url)
    warehouse_task_summary_repo.init_schema()

    crm_repo = CrmRepository(settings.db_url)
    crm_repo.init_schema()

    storage_repo = StorageWarehouseRepository(settings.db_url)
    storage_repo.init_schema()

    catalog_repo = CatalogRepository(settings.db_url)
    catalog_repo.init_schema()

    receipts_repo = WarehouseReceiptsRepository(settings.db_url, storage_repo)
    receipts_repo.init_schema()

    writeoffs_repo = WarehouseWriteoffsRepository(settings.db_url, storage_repo)
    writeoffs_repo.init_schema()

    transfers_repo = WarehouseTransfersRepository(settings.db_url, storage_repo)
    transfers_repo.init_schema()

    tasks_repo = WarehouseTasksRepository(
        settings.db_url,
        warehouse_users_repo,
        receipts_repo,
        writeoffs_repo,
        transfers_repo,
        catalog_repo,
        crm_repo,
        task_files_data_dir=settings.warehouse_task_files_data_dir,
    )
    tasks_repo.init_schema()

    packing_files_dir = Path(settings.warehouse_task_files_data_dir) / "fbs_packing"
    packing_repo = FbsPackingRepository(settings.db_url, files_data_dir=packing_files_dir)
    packing_repo.init_schema()

    def _try_bootstrap_warehouse_admin() -> None:
        login, password = resolve_warehouse_admin_credentials(settings)
        if warehouse_users_repo.count_users() > 0:
            return
        if not login or not password:
            raise HTTPException(
                status_code=503,
                detail=(
                    "В БД нет пользователей. Задайте WAREHOUSE_ADMIN_LOGIN и "
                    "WAREHOUSE_ADMIN_PASSWORD в .env."
                ),
            )
        warehouse_users_repo.ensure_bootstrap_admin(
            login,
            password,
            display_name=settings.warehouse_admin_display_name,
        )

    def _warehouse_user_from_session(request: Request) -> WarehouseUserRow | None:
        raw_id = request.session.get(_WH_SESSION_USER_KEY)
        if raw_id is None:
            return None
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        user = warehouse_users_repo.get_by_id(user_id)
        if user is None or not user.is_active:
            return None
        return user

    async def require_warehouse_user(request: Request) -> WarehouseUserRow:
        user = _warehouse_user_from_session(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Требуется вход в API (логин сотрудника склада)")
        return user

    def _login_user(login_n: str, password_n: str) -> WarehouseUserRow:
        _try_bootstrap_warehouse_admin()
        if not login_n or not password_n:
            raise HTTPException(status_code=400, detail="Введите логин и пароль")
        env_login, env_pass = resolve_warehouse_admin_credentials(settings)
        if env_login and env_pass and login_n == env_login and password_n == env_pass:
            return warehouse_users_repo.upsert_env_admin(
                login_n,
                password_n,
                display_name=settings.warehouse_admin_display_name,
            )
        user = warehouse_users_repo.authenticate(login_n, password_n)
        if user is None:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        return user

    def _public_user(user: WarehouseUserRow) -> dict[str, Any]:
        return {
            "id": user.id,
            "login": user.login,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
            "is_active": user.is_active,
        }

    require_tasks_access = make_require_tasks_access(
        settings.warehouse_tasks_api_token,
        _warehouse_user_from_session,
    )

    @app.get("/")
    async def api_root() -> dict:
        return {
            "service": "warehouse-desktop-api",
            "login": "/api/v1/login",
            "tasks": "/api/v1/tasks",
            "fbs_packing": "/api/v1/fbs-packing",
        }

    @app.get("/api/v1/health")
    async def api_health() -> dict:
        return {"ok": True, "service": "warehouse-desktop-api"}

    @app.post("/api/v1/login")
    async def api_login_json(body: LoginBody, request: Request) -> dict:
        user = _login_user(body.login.strip(), body.password.strip())
        request.session[_WH_SESSION_USER_KEY] = user.id
        return {"ok": True, "user": _public_user(user)}

    @app.post("/api/v1/login/form")
    async def api_login_form(
        request: Request,
        login: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
    ) -> dict:
        user = _login_user(login.strip(), password.strip())
        request.session[_WH_SESSION_USER_KEY] = user.id
        return {"ok": True, "user": _public_user(user)}

    @app.post("/api/v1/logout")
    async def api_logout(request: Request) -> dict[str, bool]:
        request.session.pop(_WH_SESSION_USER_KEY, None)
        return {"ok": True}

    @app.get("/api/v1/session")
    async def api_session(user: WarehouseUserRow = Depends(require_warehouse_user)) -> dict:
        fresh = warehouse_users_repo.get_by_id(user.id) or user
        return {"user": _public_user(fresh)}

    register_warehouse_tasks_routes(
        app,
        tasks_repo,
        warehouse_users_repo,
        crm_repo,
        warehouse_schedule_repo,
        warehouse_task_summary_repo,
        require_tasks_access,
        prefixes=("/api/v1/tasks",),
    )
    register_warehouse_fbs_packing_routes(
        app,
        packing_repo,
        catalog_repo,
        warehouse_users_repo,
        settings,
        None,
        require_fbs_access=require_warehouse_user,
        require_warehouse_user=require_warehouse_user,
        require_tasks_access=require_tasks_access,
        include_manager=False,
        packer_prefixes=("/api/v1/fbs-packing",),
    )
    return app
