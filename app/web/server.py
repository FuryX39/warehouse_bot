"""
HTTP API и раздача веб-страницы панели.

Авторизация:
  - В .env обязателен WEB_DASHBOARD_SECRET (пароль для входа).
  - После POST /api/login в cookie-сессии ставится флаг; все страницы и API
    (кроме /login, POST /api/login и статики) требуют эту сессию.

Маршруты:
  GET  /login         — форма входа
  POST /api/login     — проверка пароля, создание сессии
  POST /api/import_sheet — импорт остатков из Google Sheets (как /import_sheet)
  POST /api/logout    — выход
  GET  /fbs           — FBS: списки и этикетки по маркетплейсам
  GET  /dealer-analysis — сравнение Excel заказов дилера (2 периода → отчёт)
  GET  /warehouse     — новая система складского учёта (навигация, разделы в разработке)
  GET  /warehouse/login — вход в новую панель (отдельная авторизация)
  POST /api/warehouse/login — логин сотрудника новой панели
  GET  /api/warehouse/session — текущий пользователь и доступные разделы
  GET  /              — панель (редирект на /login без сессии)
  GET  /static/*      — CSS/JS (без данных; основная защита — API и /)
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app.adapters.base import is_value_configured
from app.config import Settings, resolve_warehouse_admin_credentials
from app.movement_ops import execute_movement_from_sheet
from app.movement_repository import MovementRepository
from app.repositories import InventoryRepository
from app.services import StockCoordinator
from app.barcode_label_pdf import generate_barcode_label_pdf
from app.fbs_labels_cache import pop_label_files, store_label_files
from app.fbs_assembly_order import apply_assembly_order_to_yandex_rows
from app.fbs_ship import (
    execute_fbs_ship,
    normalize_ship_scope,
    preview_fbs_ship,
    scope_label,
    sources_for_scope,
)
from app.ozon_fbs_labels import (
    apply_tsd_assembly_order,
    build_labels_zip,
    build_sorted_list_rows,
    fetch_awaiting_shipment_labels,
    filter_by_posting_range,
    get_configured_ozon_adapter,
    posting_numbers_chronological,
    posting_numbers_in_list_order,
)
from app.yandex_fbs_labels import (
    YANDEX_FBS_SUBSTATUS_OPTIONS,
    YandexFbsListRow,
    build_order_box_labels,
    build_sorted_list_rows as build_yandex_sorted_list_rows,
    fetch_awaiting_assembly_labels,
    get_configured_yandex_adapter,
    normalize_yandex_fbs_substatus,
    order_ids_in_list_order,
)
from app.nomenclature_barcodes import parse_barcodes_cell
from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.storage_warehouse_repository import StorageWarehouseRepository
from app.dealer_analysis_repository import DealerAnalysisRepository
from app.google_sheet_write import describe_fbs_google_sheets
from app.sheet_import import (
    import_nomenclature_from_google_sheet,
    import_stocks_from_google_sheet,
    import_tops_from_google_sheet,
)
from app.warehouse_permissions import (
    filter_nav_for_user,
    permissions_schema,
)
from app.warehouse_roles_repository import WarehouseRolesRepository
from app.warehouse_schedule_repository import WarehouseScheduleRepository
from app.warehouse_task_summary_repository import WarehouseTaskSummaryRepository
from app.warehouse_users_repository import WarehouseUserRow, WarehouseUsersRepository
from app.web.warehouse_barcode_print_routes import register_warehouse_barcode_print_routes
from app.web.warehouse_catalog_routes import register_warehouse_catalog_routes
from app.web.warehouse_crm_routes import register_warehouse_crm_routes
from app.web.warehouse_staff_routes import register_warehouse_staff_routes
from app.web.warehouse_storage_routes import register_warehouse_storage_routes
from app.web.warehouse_receipts_routes import register_warehouse_receipts_routes
from app.warehouse_receipts_repository import WarehouseReceiptsRepository
from app.web.warehouse_writeoffs_routes import register_warehouse_writeoffs_routes
from app.warehouse_writeoffs_repository import WarehouseWriteoffsRepository
from app.web.warehouse_transfers_routes import register_warehouse_transfers_routes
from app.warehouse_transfers_repository import WarehouseTransfersRepository
from app.web.warehouse_tasks_routes import register_warehouse_tasks_routes
from app.web.warehouse_tasks_api_auth import make_require_tasks_access
from app.warehouse_tasks_repository import WarehouseTasksRepository
from app.warehouse_stock_repository import WarehouseStockRepository
from app.web.warehouse_stock_routes import register_warehouse_stock_routes
from app.ozon_fbo_supply_repository import OzonFboSupplyRepository
from app.web.warehouse_ozon_fbo_routes import register_warehouse_ozon_fbo_routes
from app.adapters.ozon import OzonAdapter
from app.web.warehouse_admin_routes import register_warehouse_admin_routes
from app.web.warehouse_route_sheets_routes import register_warehouse_route_sheets_routes
from app.web.warehouse_repricer_routes import register_warehouse_repricer_routes
from app.web.warehouse_tools_routes import register_warehouse_tools_routes

_WEB_ROOT = Path(__file__).resolve().parent
_SESSION_COOKIE = "warehouse_session"
_SESSION_KEY_PREFIX = "warehouse_web_session_signing_v1:"
_WH_SESSION_USER_KEY = "wh_user_id"
_FBS_SHIP_SESSION_KEY = "fbs_ship_pending"
_FBS_SHIP_CODE_TTL_SECONDS = 300


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


def _session_signing_key(dashboard_secret: str) -> str:
    """Отдельный ключ подписи cookie, чтобы не класть сырой пароль в middleware."""
    return hashlib.sha256((_SESSION_KEY_PREFIX + dashboard_secret).encode("utf-8")).hexdigest()


def _parse_day(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _utc_day_start_ts(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def _utc_day_end_ts(d: date) -> int:
    n = d + timedelta(days=1)
    return int(datetime.combine(n, datetime.min.time(), tzinfo=timezone.utc).timestamp()) - 1


def _password_ok(attempt: str, secret: str) -> bool:
    a = attempt.encode("utf-8")
    b = secret.encode("utf-8")
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


_ORDER_ITEM_SOURCES = frozenset({"ozon", "yandex_market", "wildberries"})
_DEALER_XLSX_MAX_BYTES = 30 * 1024 * 1024
_DEALER_XLSX_MAGIC = b"PK\x03\x04"


def _dealer_xlsx_ok(data: bytes, filename: str) -> bool:
    if not data.startswith(_DEALER_XLSX_MAGIC):
        return False
    name = (filename or "").lower()
    return name.endswith((".xlsx", ".xlsm", ".xltx")) or not name


def _dealer_download_ascii_filename(filename: str) -> str:
    """Имя для заголовка HTTP (только ASCII — иначе 500 на latin-1)."""
    raw = (filename or "dealer_analysis.xlsx").strip() or "dealer_analysis.xlsx"
    safe = "".join(c if ord(c) < 128 and (c.isalnum() or c in "._-") else "_" for c in raw)
    if not safe.lower().endswith((".xlsx", ".xlsm", ".xltx")):
        safe = (safe.rstrip(".") or "dealer_analysis") + ".xlsx"
    return safe


def _content_disposition_attachment(filename: str) -> str:
    ascii_name = _dealer_download_ascii_filename(filename)
    utf8_name = quote((filename or ascii_name).strip() or ascii_name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


def create_dashboard_app(
    settings: Settings,
    inventory_repo: InventoryRepository,
    coordinator: StockCoordinator,
    movement_repo: MovementRepository,
    dealer_analysis_repo: DealerAnalysisRepository,
) -> FastAPI:
    dashboard_secret = (settings.web_dashboard_secret or "").strip()
    if not dashboard_secret:
        raise RuntimeError(
            "WEB_DASHBOARD_SECRET пуст: веб-панель не запускается без пароля. "
            "Задайте длинную строку в .env и перезапустите run_web.py"
        )

    app = FastAPI(title="Warehouse dashboard", version="1.0", default_response_class=Utf8JSONResponse)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_signing_key(dashboard_secret),
        session_cookie=_SESSION_COOKIE,
        max_age=86400 * 14,
        same_site="lax",
        https_only=False,
    )

    warehouse_users_repo = WarehouseUsersRepository(settings.db_url)
    warehouse_users_repo.init_schema()

    warehouse_roles_repo = WarehouseRolesRepository(settings.db_url)
    warehouse_roles_repo.init_schema()
    warehouse_roles_repo.migrate_legacy_admin_users(
        [user.id for user in warehouse_users_repo.list_users() if user.is_admin]
    )

    warehouse_schedule_repo = WarehouseScheduleRepository(settings.db_url)
    warehouse_schedule_repo.init_schema()

    warehouse_task_summary_repo = WarehouseTaskSummaryRepository(settings.db_url)
    warehouse_task_summary_repo.init_schema()

    crm_repo = CrmRepository(settings.db_url)
    crm_repo.init_schema()

    storage_repo = StorageWarehouseRepository(settings.db_url)
    storage_repo.init_schema()

    inventory_repo.attach_storage_repo(storage_repo)

    catalog_repo = CatalogRepository(settings.db_url)
    catalog_repo.init_schema()

    stock_repo = WarehouseStockRepository(settings.db_url)
    stock_repo.init_schema()

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

    ozon_fbo_repo = OzonFboSupplyRepository(settings.db_url, catalog_repo, warehouse_users_repo)
    ozon_fbo_repo.init_schema()

    ozon_adapter = next(
        (adapter for adapter in coordinator.adapters if isinstance(adapter, OzonAdapter)),
        None,
    )

    def _sync_legacy_stock_to_storage(sku: str, stock: int) -> None:
        wh_id = storage_repo.get_legacy_warehouse_id()
        if wh_id is None:
            return
        storage_repo.set_stock(int(wh_id), sku, int(stock), skip_recalc=True)

    def _recalc_stock_skus(skus: set[str]) -> None:
        stock_repo.recalculate_skus(skus)

    storage_repo.set_stock_balance_hook(_recalc_stock_skus)
    inventory_repo.set_stock_balance_hook(
        _recalc_stock_skus,
        after_stock_write=_sync_legacy_stock_to_storage,
    )

    def _env_admin_credentials() -> tuple[str, str]:
        return resolve_warehouse_admin_credentials(settings)

    def _credentials_match_env_admin(login_n: str, password_n: str) -> bool:
        env_login, env_pass = _env_admin_credentials()
        if not env_login or not env_pass:
            return False
        if login_n != env_login:
            return False
        return _password_ok(password_n, env_pass)

    def _try_bootstrap_warehouse_admin() -> None:
        login, password = _env_admin_credentials()
        if warehouse_users_repo.count_users() == 0:
            if not login or not password:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "В БД нет пользователей новой панели. Задайте WAREHOUSE_ADMIN_LOGIN и "
                        "WAREHOUSE_ADMIN_PASSWORD (или WEB_DASHBOARD_SECRET) в .env и перезапустите веб."
                    ),
                )
            warehouse_users_repo.ensure_bootstrap_admin(
                login,
                password,
                display_name=settings.warehouse_admin_display_name,
            )
            user = warehouse_users_repo.get_by_login(login)
            if user is not None:
                warehouse_roles_repo.sync_admin_role_for_user(user.id, is_admin=True)
            return
        if login and password:
            user = warehouse_users_repo.sync_env_admin(
                login,
                password,
                display_name=settings.warehouse_admin_display_name,
            )
            if user is not None:
                warehouse_roles_repo.sync_admin_role_for_user(user.id, is_admin=True)

    def _resolve_user_access(user: WarehouseUserRow) -> tuple[bool, dict[str, list[str]]]:
        return warehouse_roles_repo.resolve_user_access(
            user_id=user.id,
            is_admin=user.is_admin,
            legacy_permissions=user.permissions,
        )

    def _user_session_dict(user: WarehouseUserRow) -> dict:
        roles = warehouse_roles_repo.get_user_roles(user.id)
        role_items = [{"id": r.id, "name": r.name, "is_admin": r.is_admin} for r in roles]
        is_admin, perms = _resolve_user_access(user)
        data = warehouse_users_repo.user_to_public_dict(
            user,
            roles=role_items,
            role_ids=[r.id for r in roles],
        )
        data["is_admin"] = is_admin
        data["permissions"] = perms
        return data

    def _resolve_warehouse_user_on_login(login_n: str, password_n: str) -> WarehouseUserRow | None:
        user = warehouse_users_repo.authenticate(login_n, password_n)
        if _credentials_match_env_admin(login_n, password_n):
            user = warehouse_users_repo.upsert_env_admin(
                login_n,
                password_n,
                display_name=settings.warehouse_admin_display_name,
            )
            return user
        return user

    async def require_login(request: Request) -> None:
        if not request.session.get("authenticated"):
            raise HTTPException(status_code=401, detail="Требуется вход")

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
            raise HTTPException(status_code=401, detail="Требуется вход в новую панель")
        return user

    async def require_fbs_access(request: Request) -> None:
        """Разрешить FBS API из старой или новой авторизованной панели."""
        if request.session.get("authenticated"):
            return
        user = _warehouse_user_from_session(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Требуется вход")
        is_admin, permissions = _resolve_user_access(user)
        if not is_admin and "fbs" not in permissions.get("marketplaces", []):
            raise HTTPException(status_code=403, detail="Нет доступа к разделу FBS")

    async def require_warehouse_admin(
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> WarehouseUserRow:
        is_admin, _ = _resolve_user_access(user)
        if not is_admin:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    def _warehouse_session_payload(user: WarehouseUserRow) -> dict:
        is_admin, perms = _resolve_user_access(user)
        payload: dict = {
            "user": _user_session_dict(user),
            "nav": filter_nav_for_user(is_admin=is_admin, permissions=perms),
        }
        if is_admin:
            payload["permissions_schema"] = permissions_schema()
        return payload

    @app.get("/login")
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse(url="/", status_code=302)
        path = _WEB_ROOT / "templates" / "login.html"
        if not path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон входа не найден")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/dealer-analysis")
    async def dealer_analysis_page(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/login", status_code=302)
        html_path = _WEB_ROOT / "templates" / "dealer_analysis.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон анализа дилера не найден")
        return FileResponse(
            html_path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    def _warehouse_html_response():
        html_path = _WEB_ROOT / "templates" / "warehouse.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон складского учёта не найден")
        return FileResponse(
            html_path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/warehouse/login")
    async def warehouse_login_page(request: Request):
        _try_bootstrap_warehouse_admin()
        if _warehouse_user_from_session(request) is not None:
            return RedirectResponse(url="/warehouse", status_code=302)
        path = _WEB_ROOT / "templates" / "warehouse_login.html"
        if not path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон входа новой панели не найден")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/warehouse")
    @app.get("/warehouse/")
    async def warehouse_page(request: Request):
        _try_bootstrap_warehouse_admin()
        if _warehouse_user_from_session(request) is None:
            return RedirectResponse(url="/warehouse/login", status_code=302)
        return _warehouse_html_response()

    @app.post("/api/warehouse/login")
    async def api_warehouse_login(
        login: Annotated[str, Form()],
        password: Annotated[str, Form()],
        request: Request,
    ) -> dict[str, bool]:
        _try_bootstrap_warehouse_admin()
        login_n = login.strip()
        password_n = password.strip()
        if not login_n or not password_n:
            raise HTTPException(status_code=400, detail="Введите логин и пароль")
        user = _resolve_warehouse_user_on_login(login_n, password_n)
        if user is None:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        request.session[_WH_SESSION_USER_KEY] = user.id
        return {"ok": True}

    @app.post("/api/warehouse/logout")
    async def api_warehouse_logout(request: Request) -> dict[str, bool]:
        request.session.pop(_WH_SESSION_USER_KEY, None)
        return {"ok": True}

    @app.get("/api/warehouse/session")
    async def api_warehouse_session(
        user: WarehouseUserRow = Depends(require_warehouse_user),
    ) -> dict:
        _try_bootstrap_warehouse_admin()
        fresh = warehouse_users_repo.get_by_id(user.id) or user
        return _warehouse_session_payload(fresh)

    register_warehouse_staff_routes(
        app,
        warehouse_users_repo,
        warehouse_roles_repo,
        warehouse_schedule_repo,
        require_warehouse_admin,
    )
    register_warehouse_admin_routes(app, require_warehouse_admin)
    register_warehouse_crm_routes(app, crm_repo, require_warehouse_user)
    register_warehouse_storage_routes(app, storage_repo, require_warehouse_user)
    register_warehouse_barcode_print_routes(app, require_warehouse_user)
    register_warehouse_catalog_routes(app, catalog_repo, require_warehouse_user, stock_repo, crm_repo)
    register_warehouse_stock_routes(app, stock_repo, require_warehouse_user)
    register_warehouse_ozon_fbo_routes(
        app, ozon_fbo_repo, require_warehouse_user, ozon_adapter, crm_repo
    )
    register_warehouse_route_sheets_routes(app, require_warehouse_user)
    register_warehouse_repricer_routes(app, catalog_repo, crm_repo, require_warehouse_user)
    register_warehouse_tools_routes(
        app,
        catalog_repo,
        require_warehouse_user,
        google_service_account_file=settings.google_service_account_file,
    )
    register_warehouse_receipts_routes(
        app,
        receipts_repo,
        catalog_repo,
        storage_repo,
        crm_repo,
        require_warehouse_user,
    )
    register_warehouse_writeoffs_routes(
        app,
        writeoffs_repo,
        catalog_repo,
        storage_repo,
        crm_repo,
        require_warehouse_user,
    )
    register_warehouse_transfers_routes(
        app,
        transfers_repo,
        catalog_repo,
        storage_repo,
        crm_repo,
        require_warehouse_user,
    )
    require_tasks_access = make_require_tasks_access(
        settings.warehouse_tasks_api_token,
        _warehouse_user_from_session,
    )
    register_warehouse_tasks_routes(
        app,
        tasks_repo,
        warehouse_users_repo,
        crm_repo,
        warehouse_schedule_repo,
        warehouse_task_summary_repo,
        require_tasks_access,
    )

    @app.get("/fbs")
    async def fbs_page(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/login", status_code=302)
        html_path = _WEB_ROOT / "templates" / "fbs.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон FBS не найден")
        return FileResponse(
            html_path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/")
    async def index_page(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/login", status_code=302)
        html_path = _WEB_ROOT / "templates" / "index.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Шаблон панели не найден")
        return FileResponse(
            html_path,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.post("/api/login")
    async def api_login(
        password: Annotated[str, Form()],
        request: Request,
    ) -> dict[str, bool]:
        """Пароль через form field (не JSON): так надёжнее с Request/сессией и прокси."""
        attempt = password.strip().lstrip("\ufeff")
        if not attempt:
            raise HTTPException(status_code=400, detail="Введите пароль")
        if not _password_ok(attempt, dashboard_secret):
            raise HTTPException(status_code=401, detail="Неверный пароль")
        request.session["authenticated"] = True
        return {"ok": True}

    @app.post("/api/logout", dependencies=[Depends(require_login)])
    async def api_logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    @app.get("/api/health", dependencies=[Depends(require_login)])
    async def health() -> dict:
        return {
            "status": "ok",
            "dealer_analysis": True,
        }

    @app.get("/api/status", dependencies=[Depends(require_login)])
    async def api_status() -> dict:
        adapters = [{"name": a.name, "configured": bool(a.is_configured())} for a in coordinator.adapters]
        last_run = coordinator.last_run_at.isoformat() if coordinator.last_run_at else None
        return {
            "last_run_at": last_run,
            "last_error": coordinator.last_error,
            "last_warnings": list(coordinator.last_warnings or []),
            "adapters": adapters,
            "telegram_configured": bool(settings.telegram_bot_token),
            "stock_sync_enabled": bool(settings.stock_sync_enabled),
        }

    @app.get("/api/inventory", dependencies=[Depends(require_login)])
    async def api_inventory() -> dict:
        rows = inventory_repo.get_inventory_snapshot()
        return {
            "items": [
                {
                    "sku": r.sku,
                    "name": r.name,
                    "image_url": r.image_url,
                    "stock": int(r.stock),
                    "reserve": int(r.reserve),
                    "available": int(r.available),
                    "is_top": bool(r.is_top),
                }
                for r in rows
            ],
        }

    @app.get("/api/missing_tops", dependencies=[Depends(require_login)])
    async def api_missing_tops(
        threshold: Annotated[int, Query(description="Порог доступного остатка (меньше этого числа)")]
    ) -> dict:
        if threshold < 0:
            raise HTTPException(status_code=400, detail="threshold должен быть >= 0")
        rows = inventory_repo.get_missing_top_items(int(threshold))
        return {
            "threshold": int(threshold),
            "count": len(rows),
            "items": [
                {
                    "sku": r.sku,
                    "name": r.name,
                    "image_url": r.image_url,
                    "stock": int(r.stock),
                    "reserve": int(r.reserve),
                    "available": int(r.available),
                    "is_top": bool(r.is_top),
                }
                for r in rows
            ],
        }

    class NomenclatureRowIn(BaseModel):
        name: str = ""
        image_url: str = ""
        barcodes: list[str] = Field(default_factory=list)

    class NomenclatureUpsertBody(BaseModel):
        items: dict[str, NomenclatureRowIn] = Field(default_factory=dict)

    @app.get("/api/nomenclature", dependencies=[Depends(require_login)])
    async def api_nomenclature_list() -> dict:
        rows = inventory_repo.list_nomenclature_all()
        return {
            "rows": [
                {
                    "sku": sku,
                    "name": name if name else "",
                    "image_url": img,
                    "barcodes": barcodes,
                }
                for sku, name, img, barcodes in rows
            ],
        }

    @app.post("/api/nomenclature", dependencies=[Depends(require_login)])
    async def api_nomenclature_upsert(body: NomenclatureUpsertBody) -> dict:
        """Массовая запись номенклатуры (артикул → название, картинка, баркоды)."""
        if len(body.items) > 20000:
            raise HTTPException(status_code=400, detail="Не более 20000 позиций за один запрос")
        payload: dict[str, tuple[str, str, list[str]]] = {}
        for sku_raw, row in body.items.items():
            sku = str(sku_raw).strip()
            if not sku:
                continue
            codes = [str(b).strip() for b in (row.barcodes or []) if str(b).strip()]
            payload[sku] = ((row.name or "").strip(), (row.image_url or "").strip(), codes)
        n = inventory_repo.upsert_nomenclature_items(payload)
        return {"upserted": n}

    @app.put("/api/nomenclature", dependencies=[Depends(require_login)])
    async def api_nomenclature_put(
        sku: Annotated[str, Form()],
        name: str = Form(default=""),
        image_url: str = Form(default=""),
        barcodes: str = Form(default=""),
    ) -> dict:
        """Сохранение одной позиции номенклатуры из веб-формы (application/x-www-form-urlencoded)."""
        sku_n = str(sku or "").strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой артикул")
        if len(sku_n) > 128:
            raise HTTPException(status_code=400, detail="Артикул не длиннее 128 символов")
        title = str(name or "").strip()
        if len(title) > 512:
            raise HTTPException(status_code=400, detail="Название не длиннее 512 символов")
        img = str(image_url or "").strip()
        if len(img) > 2048:
            raise HTTPException(status_code=400, detail="Ссылка на картинку не длиннее 2048 символов")
        codes = parse_barcodes_cell(str(barcodes or ""))
        n = inventory_repo.upsert_nomenclature_items({sku_n: (title, img, codes)})
        return {"upserted": n, "sku": sku_n}

    @app.get("/api/ozon/awaiting-shipment", dependencies=[Depends(require_fbs_access)])
    async def api_ozon_awaiting_shipment_list(
        first_posting: Annotated[str, Query(description="Первый номер отправления в диапазоне")] = "",
        last_posting: Annotated[str, Query(description="Последний номер отправления в диапазоне")] = "",
    ) -> dict:
        """Список FBS-отправлений Ozon в статусе «ожидает отгрузки» (awaiting_deliver)."""
        adapter = get_configured_ozon_adapter(coordinator)
        if adapter is None:
            raise HTTPException(status_code=400, detail="Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)")
        loop = asyncio.get_running_loop()
        try:
            postings = await loop.run_in_executor(
                None, lambda: adapter.list_awaiting_shipment_postings()
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ozon API: {exc}") from exc
        list_rows = build_sorted_list_rows(postings)
        order = posting_numbers_chronological(postings)
        try:
            list_rows, order = filter_by_posting_range(
                list_rows,
                order,
                first_posting=first_posting,
                last_posting=last_posting,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        list_rows, assembly_warnings = apply_tsd_assembly_order(
            list_rows,
            default_stocks_sheet_url=settings.default_stocks_sheet_url,
            google_service_account_file=settings.google_service_account_file,
            assembly_sheet_name=settings.fbs_assembly_sheet_name,
            assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
        )
        by_pn = {p.posting_number: p for p in postings}
        postings_ordered = [by_pn[pn] for pn in order if pn in by_pn]
        return {
            "count": len(postings_ordered),
            "status": "awaiting_deliver",
            "warnings": assembly_warnings,
            "list_rows": [
                {
                    "seq": r.seq,
                    "sku": r.sku,
                    "quantity": r.quantity,
                    "posting_number": r.posting_number,
                }
                for r in list_rows
            ],
            "postings": [
                {
                    "posting_number": p.posting_number,
                    "status": p.status,
                    "lines": [{"sku": sku, "quantity": qty} for sku, qty in p.lines],
                }
                for p in postings_ordered
            ],
        }

    def _fbs_label_files_response(label_files: list[tuple[str, bytes]]) -> Response:
        if len(label_files) == 1:
            name, pdf = label_files[0]
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        zip_bytes = build_labels_zip(label_files)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ozon_awaiting_labels.zip"'},
        )

    @app.post("/api/fbs/ozon/generate", dependencies=[Depends(require_fbs_access)])
    async def api_fbs_ozon_generate(
        first_posting: Annotated[str, Form()] = "",
        last_posting: Annotated[str, Form()] = "",
    ) -> dict:
        """FBS Ozon: список в Google Таблице + этикетки (form: опционально first/last posting)."""
        adapter = get_configured_ozon_adapter(coordinator)
        if adapter is None:
            raise HTTPException(status_code=400, detail="Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)")
        loop = asyncio.get_running_loop()
        first_p = (first_posting or "").strip()
        last_p = (last_posting or "").strip()
        try:
            bundle = await loop.run_in_executor(
                None,
                lambda: fetch_awaiting_shipment_labels(
                    adapter,
                    default_stocks_sheet_url=settings.default_stocks_sheet_url,
                    fbs_list_sheet_url=settings.fbs_list_sheet_url,
                    google_service_account_file=settings.google_service_account_file,
                    fbs_list_template_sheet=settings.fbs_list_template_sheet,
                    fbs_assembly_sheet_name=settings.fbs_assembly_sheet_name,
                    assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
                    ozon_label_rotate_degrees=settings.ozon_label_rotate_degrees,
                    first_posting_number=first_p or None,
                    last_posting_number=last_p or None,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ozon API: {exc}") from exc
        if not bundle.postings:
            raise HTTPException(status_code=404, detail="Нет отправлений awaiting_deliver")
        labels_token: str | None = None
        if bundle.label_files:
            labels_token = store_label_files(bundle.label_files)
        elif bundle.warnings:
            raise HTTPException(
                status_code=502,
                detail="Не удалось получить PDF-этикетки: " + "; ".join(bundle.warnings[:5]),
            )
        else:
            raise HTTPException(status_code=502, detail="Не удалось получить PDF-этикетки")
        return {
            "count": len(bundle.list_rows),
            "status": "awaiting_deliver",
            "posting_range": {
                "first": first_p or None,
                "last": last_p or None,
            },
            "warnings": bundle.warnings,
            "list_rows": [
                {
                    "seq": r.seq,
                    "sku": r.sku,
                    "quantity": r.quantity,
                    "posting_number": r.posting_number,
                }
                for r in bundle.list_rows
            ],
            "sheet_title": bundle.sheet_title,
            "sheet_url": bundle.sheet_url,
            "labels_token": labels_token,
        }

    @app.get("/api/fbs/ozon/labels", dependencies=[Depends(require_fbs_access)])
    async def api_fbs_ozon_labels(
        token: Annotated[str, Query(description="Токен после POST /api/fbs/ozon/generate")],
    ) -> Response:
        label_files = pop_label_files(token)
        if not label_files:
            raise HTTPException(status_code=404, detail="Ссылка на этикетки устарела или уже использована")
        return _fbs_label_files_response(label_files)

    @app.get("/api/yandex/awaiting-assembly", dependencies=[Depends(require_fbs_access)])
    async def api_yandex_awaiting_assembly_list(
        item_limit: Annotated[int | None, Query(ge=1)] = None,
        order_substatus: Annotated[str, Query()] = "STARTED",
    ) -> dict:
        """FBS-заказы Yandex выбранного подстатуса PROCESSING."""
        try:
            order_substatus = normalize_yandex_fbs_substatus(order_substatus)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        adapter = get_configured_yandex_adapter(coordinator)
        if adapter is None:
            raise HTTPException(
                status_code=400,
                detail="Yandex Market API не настроен (YANDEX_CAMPAIGN_ID / YANDEX_API_KEY)",
            )
        loop = asyncio.get_running_loop()
        try:
            orders = await loop.run_in_executor(
                None,
                lambda: adapter.list_awaiting_assembly_orders(
                    substatus=order_substatus
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Yandex API: {exc}") from exc
        list_rows = build_yandex_sorted_list_rows(orders)
        list_rows, assembly_warnings = await loop.run_in_executor(
            None,
            lambda: apply_assembly_order_to_yandex_rows(
                list_rows,
                default_stocks_sheet_url=settings.default_stocks_sheet_url,
                google_service_account_file=settings.google_service_account_file,
                assembly_sheet_name=settings.fbs_assembly_sheet_name,
                assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
                row_factory=YandexFbsListRow,
            ),
        )
        available_count = len(list_rows)
        if item_limit is not None:
            list_rows = list_rows[:item_limit]
        order_ids = order_ids_in_list_order(list_rows)
        by_id = {o.order_id: o for o in orders}
        orders_ordered = [by_id[oid] for oid in order_ids if oid in by_id]
        order_box_labels = build_order_box_labels(list_rows, orders)
        return {
            "count": len(list_rows),
            "available_count": available_count,
            "orders_count": len(orders_ordered),
            "status": "PROCESSING",
            "substatus": order_substatus,
            "warnings": assembly_warnings,
            "list_rows": [
                {
                    "seq": r.seq,
                    "sku": r.sku,
                    "quantity": r.quantity,
                    "order_id": r.order_id,
                    "order_display": order_display,
                    "posting_number": r.order_id,
                }
                for r, order_display in zip(list_rows, order_box_labels)
            ],
            "orders": [
                {
                    "order_id": o.order_id,
                    "status": o.status,
                    "substatus": o.substatus,
                    "lines": [{"sku": sku, "quantity": qty} for sku, qty in o.lines],
                }
                for o in orders_ordered
            ],
        }

    @app.post("/api/fbs/yandex/generate", dependencies=[Depends(require_fbs_access)])
    async def api_fbs_yandex_generate(
        item_limit: Annotated[int | None, Form(ge=1)] = None,
        order_substatus: Annotated[str, Form()] = "STARTED",
    ) -> dict:
        """FBS Yandex: список в Google Таблице + этикетки (без тела запроса)."""
        try:
            order_substatus = normalize_yandex_fbs_substatus(order_substatus)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        adapter = get_configured_yandex_adapter(coordinator)
        if adapter is None:
            raise HTTPException(
                status_code=400,
                detail="Yandex Market API не настроен (YANDEX_CAMPAIGN_ID / YANDEX_API_KEY)",
            )
        loop = asyncio.get_running_loop()
        try:
            bundle = await loop.run_in_executor(
                None,
                lambda: fetch_awaiting_assembly_labels(
                    adapter,
                    substatus=order_substatus,
                    fbs_list_sheet_url=settings.fbs_list_sheet_url,
                    google_service_account_file=settings.google_service_account_file,
                    fbs_list_template_sheet=settings.fbs_list_template_sheet,
                    yandex_label_format=settings.yandex_label_format,
                    yandex_label_rotate_degrees=settings.yandex_label_rotate_degrees,
                    default_stocks_sheet_url=settings.default_stocks_sheet_url,
                    fbs_assembly_sheet_name=settings.fbs_assembly_sheet_name,
                    assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
                    max_units=item_limit,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Yandex API: {exc}") from exc
        if not bundle.orders:
            status_title = YANDEX_FBS_SUBSTATUS_OPTIONS[order_substatus]
            raise HTTPException(
                status_code=404,
                detail=f"Нет заказов PROCESSING + {order_substatus} («{status_title}»)",
            )
        labels_token: str | None = None
        if bundle.label_files:
            labels_token = store_label_files(bundle.label_files)
        elif bundle.warnings:
            raise HTTPException(
                status_code=502,
                detail="Не удалось получить PDF-этикетки: " + "; ".join(bundle.warnings[:5]),
            )
        else:
            raise HTTPException(status_code=502, detail="Не удалось получить PDF-этикетки")
        order_box_labels = build_order_box_labels(bundle.list_rows, bundle.orders)
        return {
            "count": len(bundle.list_rows),
            "available_count": bundle.available_units,
            "orders_count": len(bundle.orders),
            "status": "PROCESSING",
            "substatus": order_substatus,
            "list_rows": [
                {
                    "seq": r.seq,
                    "sku": r.sku,
                    "quantity": r.quantity,
                    "order_id": r.order_id,
                    "order_display": order_display,
                    "posting_number": r.order_id,
                }
                for r, order_display in zip(bundle.list_rows, order_box_labels)
            ],
            "sheet_title": bundle.sheet_title,
            "sheet_url": bundle.sheet_url,
            "warnings": bundle.warnings,
            "labels_token": labels_token,
        }

    @app.get("/api/fbs/yandex/labels", dependencies=[Depends(require_fbs_access)])
    async def api_fbs_yandex_labels(
        token: Annotated[str, Query(description="Токен после POST /api/fbs/yandex/generate")],
    ) -> Response:
        label_files = pop_label_files(token)
        if not label_files:
            raise HTTPException(status_code=404, detail="Ссылка на этикетки устарела или уже использована")
        if len(label_files) == 1:
            name, pdf = label_files[0]
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        zip_bytes = build_labels_zip(label_files)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="yandex_awaiting_labels.zip"'},
        )

    def _purge_fbs_ship_pending(session: dict) -> None:
        entry = session.get(_FBS_SHIP_SESSION_KEY)
        if not entry:
            return
        if float(entry.get("expires_at") or 0) < time.time():
            session.pop(_FBS_SHIP_SESSION_KEY, None)

    def _ship_result_payload(scope: str, result: dict) -> dict:
        by_source = result.get("by_source") or {}
        lines = []
        for src in sorted(by_source.keys()):
            st = by_source[src]
            lines.append(
                {
                    "source": src,
                    "reserves_shipped": int(st.get("reserves_shipped", 0)),
                    "reserved_units": int(st.get("reserved_units", 0)),
                    "affected_skus": int(st.get("affected_skus", 0)),
                }
            )
        sync_result = result.get("sync_result") or {}
        return {
            "scope": scope,
            "scope_label": scope_label(scope),
            "total_reserves_shipped": int(result.get("total_reserves_shipped", 0)),
            "total_reserved_units": int(result.get("total_reserved_units", 0)),
            "total_skus": int(result.get("total_skus", 0)),
            "by_source": lines,
            "source_errors": list(result.get("source_errors") or []),
            "movement_ids": dict(result.get("movement_ids") or {}),
            "sync_warnings": list(sync_result.get("adapter_errors") or []),
        }

    @app.get("/api/fbs/ship/preview", dependencies=[Depends(require_login)])
    async def api_fbs_ship_preview(
        scope: Annotated[str, Query(description="all | ozon | wildberries | yandex_market")] = "all",
    ) -> dict:
        try:
            scope_n = normalize_ship_scope(scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sources = sources_for_scope(scope_n)
        loop = asyncio.get_running_loop()
        preview = await loop.run_in_executor(
            None,
            lambda: preview_fbs_ship(inventory_repo, coordinator, sources),
        )
        return {
            "scope": scope_n,
            "scope_label": scope_label(scope_n),
            **preview,
        }

    @app.post("/api/fbs/ship/request", dependencies=[Depends(require_login)])
    async def api_fbs_ship_request(
        request: Request,
        scope: Annotated[str, Form()],
    ) -> dict:
        """Запрос кода подтверждения (form, без JSON-тела)."""
        try:
            scope_n = normalize_ship_scope(scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        code = secrets.token_hex(3).upper()
        request.session[_FBS_SHIP_SESSION_KEY] = {
            "scope": scope_n,
            "code": code,
            "expires_at": time.time() + _FBS_SHIP_CODE_TTL_SECONDS,
        }
        return {
            "scope": scope_n,
            "scope_label": scope_label(scope_n),
            "code": code,
            "expires_in": _FBS_SHIP_CODE_TTL_SECONDS,
        }

    @app.post("/api/fbs/ship/confirm", dependencies=[Depends(require_login)])
    async def api_fbs_ship_confirm(
        request: Request,
        scope: Annotated[str, Form()],
        code: Annotated[str, Form()],
    ) -> dict:
        """Отгрузка после подтверждения кодом (как /ship_* в боте)."""
        try:
            scope_n = normalize_ship_scope(scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _purge_fbs_ship_pending(request.session)
        entry = request.session.get(_FBS_SHIP_SESSION_KEY)
        if not entry or entry.get("scope") != scope_n:
            raise HTTPException(
                status_code=400,
                detail="Сначала запросите код: нажмите кнопку отгрузки без ввода кода",
            )
        if float(entry.get("expires_at") or 0) < time.time():
            request.session.pop(_FBS_SHIP_SESSION_KEY, None)
            raise HTTPException(status_code=400, detail="Код истёк. Запросите новый код.")
        user_code = str(code or "").strip().upper()
        if user_code != str(entry.get("code") or "").upper():
            raise HTTPException(status_code=400, detail="Неверный код. Отгрузка не выполнена.")
        request.session.pop(_FBS_SHIP_SESSION_KEY, None)

        sources = sources_for_scope(scope_n)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: execute_fbs_ship(
                    inventory_repo,
                    coordinator,
                    movement_repo,
                    sources,
                    sync_before=True,
                    journal_source="web",
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Отгрузка: {exc}") from exc
        return _ship_result_payload(scope_n, result)

    @app.get("/api/ozon/awaiting-shipment-labels", dependencies=[Depends(require_login)])
    async def api_ozon_awaiting_shipment_labels() -> Response:
        """ZIP с PDF-этикетками для отправлений awaiting_deliver."""
        adapter = get_configured_ozon_adapter(coordinator)
        if adapter is None:
            raise HTTPException(status_code=400, detail="Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)")
        loop = asyncio.get_running_loop()
        try:
            bundle = await loop.run_in_executor(
                None,
                lambda: fetch_awaiting_shipment_labels(
                    adapter,
                    default_stocks_sheet_url=settings.default_stocks_sheet_url,
                    fbs_list_sheet_url=settings.fbs_list_sheet_url,
                    google_service_account_file=settings.google_service_account_file,
                    fbs_list_template_sheet=settings.fbs_list_template_sheet,
                    fbs_assembly_sheet_name=settings.fbs_assembly_sheet_name,
                    assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
                    ozon_label_rotate_degrees=settings.ozon_label_rotate_degrees,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ozon API: {exc}") from exc
        if not bundle.postings:
            raise HTTPException(status_code=404, detail="Нет отправлений awaiting_deliver")
        if not bundle.label_files:
            detail = "Не удалось получить PDF-этикетки"
            if bundle.warnings:
                detail += ": " + "; ".join(bundle.warnings[:5])
            raise HTTPException(status_code=502, detail=detail)
        return _fbs_label_files_response(bundle.label_files)

    @app.get("/api/barcode-label", dependencies=[Depends(require_login)])
    async def api_barcode_label(
        sku: Annotated[str, Query()],
        barcode: Annotated[str, Query()],
    ) -> Response:
        """PDF-этикетка Code 128 для скачивания (без отправки на печать)."""
        sku_n = str(sku or "").strip()
        bc = str(barcode or "").strip()
        if not sku_n or not bc:
            raise HTTPException(status_code=400, detail="Укажите sku и barcode")
        allowed = inventory_repo.get_barcodes_for_sku(sku_n)
        if bc not in allowed:
            raise HTTPException(status_code=404, detail="Штрихкод не привязан к этому артикулу")
        meta = inventory_repo.get_nomenclature_meta_for_skus([sku_n]).get(sku_n, {})
        product_name = str(meta.get("name") or "")
        try:
            pdf_bytes = await asyncio.to_thread(
                generate_barcode_label_pdf,
                bc,
                sku=sku_n,
                product_name=product_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        safe_bc = "".join(c if c.isalnum() or c in "-_" else "_" for c in bc)[:48]
        filename = f"barcode_{sku_n}_{safe_bc}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/orders", dependencies=[Depends(require_login)])
    async def api_orders(
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
        source: Annotated[str | None, Query(description="ozon | yandex_market | wildberries")] = None,
        sku: Annotated[str | None, Query(description="Подстрока артикула")] = None,
        order: Annotated[str | None, Query(description="Подстрока номера заказа (external_order_id)")] = None,
        limit: int = 5000,
    ) -> dict:
        from_ts: int | None = None
        to_ts: int | None = None
        d_from = _parse_day(from_date)
        d_to = _parse_day(to_date)
        if d_from is not None and d_to is not None:
            if d_from > d_to:
                d_from, d_to = d_to, d_from
            from_ts = _utc_day_start_ts(d_from)
            to_ts = _utc_day_end_ts(d_to)
        elif d_from is not None:
            from_ts = _utc_day_start_ts(d_from)
            to_ts = _utc_day_end_ts(datetime.now(timezone.utc).date())
        elif d_to is not None:
            to_ts = _utc_day_end_ts(d_to)

        source_f: str | None = None
        if source and str(source).strip():
            s = str(source).strip().lower()
            if s not in _ORDER_ITEM_SOURCES:
                raise HTTPException(
                    status_code=400,
                    detail=f"source должен быть одним из: {', '.join(sorted(_ORDER_ITEM_SOURCES))}",
                )
            source_f = s

        rows = inventory_repo.list_order_items(
            from_ts,
            to_ts,
            source=source_f,
            sku_contains=sku,
            order_contains=order,
            limit=min(max(1, limit), 20000),
        )
        return {
            "rows": [
                {
                    "source": a,
                    "external_order_id": b,
                    "sku": c,
                    "quantity": int(d),
                    "state": e,
                    "first_seen_ts": int(f),
                    "last_seen_ts": int(g),
                }
                for a, b, c, d, e, f, g in rows
            ],
        }

    @app.post("/api/import_sheet", dependencies=[Depends(require_login)])
    async def api_import_sheet(
        url: str | None = Form(default=None),
    ) -> dict:
        """
        Импорт остатков из Google Sheets (лист `stocks`), как команда бота /import_sheet [URL].
        Поле формы `url` (пусто = DEFAULT_STOCKS_SHEET_URL из .env).
        """
        raw = (url or "").strip()
        sheet_url = raw or (settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL таблицы в форме или задайте DEFAULT_STOCKS_SHEET_URL в .env",
            )

        def _run() -> dict:
            stocks_by_sku, warnings = import_stocks_from_google_sheet(sheet_url)
            if not stocks_by_sku:
                return {
                    "updated": 0,
                    "sku_in_sheet": 0,
                    "warnings": warnings,
                    "message": "Импорт завершён: валидных строк с остатками не найдено.",
                }
            updated = inventory_repo.upsert_stocks(stocks_by_sku)
            return {
                "updated": updated,
                "sku_in_sheet": len(stocks_by_sku),
                "warnings": warnings[:40],
                "warnings_more": max(0, len(warnings) - 40),
            }

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка импорта: {exc}") from exc

    @app.post("/api/import_tops_sheet", dependencies=[Depends(require_login)])
    async def api_import_tops_sheet(
        url: str | None = Form(default=None),
    ) -> dict:
        """
        Импорт top-флагов из Google Sheets (лист `tops`, колонка sku).
        Поле формы `url` (пусто = DEFAULT_STOCKS_SHEET_URL из .env).
        """
        raw = (url or "").strip()
        sheet_url = raw or (settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL таблицы в форме или задайте DEFAULT_STOCKS_SHEET_URL в .env",
            )

        def _run() -> dict:
            top_skus, warnings = import_tops_from_google_sheet(sheet_url)
            result = inventory_repo.set_top_flags_by_skus(top_skus)
            return {
                **result,
                "warnings": warnings[:40],
                "warnings_more": max(0, len(warnings) - 40),
            }

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка импорта top-товаров: {exc}") from exc

    @app.post("/api/import_nomenclature_sheet", dependencies=[Depends(require_login)])
    async def api_import_nomenclature_sheet(
        url: str | None = Form(default=None),
    ) -> dict:
        """
        Импорт номенклатуры из Google Sheets: тот же spreadsheet, что в .env (или url в форме),
        лист «nomenclature»: sku, name, опционально image_url, barcodes (ШК через запятую: 123,456).
        """
        raw = (url or "").strip()
        sheet_url = raw or (settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL таблицы в форме или задайте DEFAULT_STOCKS_SHEET_URL в .env",
            )

        def _run_nom() -> dict:
            items, warnings = import_nomenclature_from_google_sheet(sheet_url)
            if not items:
                return {
                    "updated": 0,
                    "sku_in_sheet": 0,
                    "warnings": warnings,
                    "message": "Импорт завершён: в таблице не найдено валидных строк.",
                }
            updated = inventory_repo.upsert_nomenclature_items(items)
            with_barcodes = sum(1 for _sku, row in items.items() if row[2])
            return {
                "updated": updated,
                "sku_in_sheet": len(items),
                "with_barcodes": with_barcodes,
                "warnings": warnings[:40],
                "warnings_more": max(0, len(warnings) - 40),
            }

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run_nom)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка импорта номенклатуры: {exc}") from exc

    @app.post("/api/sync", dependencies=[Depends(require_login)])
    async def api_sync(mode: str = Form(default="auto")) -> dict:
        mode_l = (mode or "auto").strip().lower()
        if mode_l not in ("auto", "delta", "full"):
            raise HTTPException(status_code=400, detail="mode: auto, delta или full")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: coordinator.sync_cycle(mode_l))

    @app.get("/api/movements", dependencies=[Depends(require_login)])
    async def api_movements_list(
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        from_ts: int | None = None
        to_ts: int | None = None
        d_from = _parse_day(from_date)
        d_to = _parse_day(to_date)
        if d_from is not None and d_to is not None:
            if d_from > d_to:
                d_from, d_to = d_to, d_from
            from_ts = _utc_day_start_ts(d_from)
            to_ts = _utc_day_end_ts(d_to)
        elif d_from is not None:
            from_ts = _utc_day_start_ts(d_from)
            to_ts = _utc_day_end_ts(datetime.now(timezone.utc).date())
        elif d_to is not None:
            to_ts = _utc_day_end_ts(d_to)

        rows = movement_repo.list_movements(
            from_ts=from_ts,
            to_ts=to_ts,
            limit=min(max(1, limit), 500),
            offset=max(0, offset),
        )
        return {
            "rows": [
                {
                    "id": r.id,
                    "created_at_ts": r.created_at_ts,
                    "direction": r.direction,
                    "direction_label": r.direction_label,
                    "source": r.source,
                    "sheet_url": r.sheet_url,
                    "title": r.title,
                    "comment": r.comment,
                    "sku_count": r.sku_count,
                    "total_quantity": r.total_quantity,
                }
                for r in rows
            ],
        }

    @app.get("/api/movements/{movement_id}", dependencies=[Depends(require_login)])
    async def api_movement_detail(movement_id: int) -> dict:
        detail = movement_repo.get_movement(movement_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        line_skus = [ln.sku for ln in detail.lines]
        nom_meta = inventory_repo.get_nomenclature_meta_for_skus(line_skus)
        lines_out = []
        for ln in detail.lines:
            meta = nom_meta.get(ln.sku, {})
            lines_out.append(
                {
                    "sku": ln.sku,
                    "name": str(meta.get("name") or ""),
                    "barcodes": list(meta.get("barcodes") or []),
                    "quantity": ln.quantity,
                    "delta": ln.delta,
                }
            )
        return {
            "id": detail.id,
            "created_at_ts": detail.created_at_ts,
            "direction": detail.direction,
            "direction_label": detail.direction_label,
            "source": detail.source,
            "sheet_url": detail.sheet_url,
            "title": detail.title,
            "title_is_default": detail.title_is_default,
            "comment": detail.comment,
            "sku_count": detail.sku_count,
            "total_quantity": detail.total_quantity,
            "warnings": detail.warnings,
            "lines": lines_out,
        }

    @app.patch("/api/movements/{movement_id}", dependencies=[Depends(require_login)])
    async def api_movement_patch(
        movement_id: int,
        title: str | None = Form(default=None),
        comment: str | None = Form(default=None),
        update_title: bool = Form(default=False),
        update_comment: bool = Form(default=False),
    ) -> dict:
        if not update_title and not update_comment:
            raise HTTPException(status_code=400, detail="Укажите title и/или comment для изменения")
        ok = movement_repo.update_movement_meta(
            movement_id,
            title=title,
            comment=comment,
            update_title=update_title,
            update_comment=update_comment,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        detail = movement_repo.get_movement(movement_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Перемещение не найдено")
        return {
            "id": detail.id,
            "title": detail.title,
            "comment": detail.comment,
        }

    @app.post("/api/movement", dependencies=[Depends(require_login)])
    async def api_movement_apply(
        direction: Annotated[str, Form()],
        url: str | None = Form(default=None),
        title: str | None = Form(default=None),
        comment: str | None = Form(default=None),
    ) -> dict:
        """Перемещение из Google Sheets (лист movement), как /movement в боте."""
        raw_dir = (direction or "").strip().lower()
        sign = 1 if raw_dir in {"+", "add", "plus", "in", "приход", "прибавить", "плюс"} else None
        if sign is None:
            sign = -1 if raw_dir in {
                "-",
                "sub",
                "subtract",
                "minus",
                "out",
                "расход",
                "отнять",
                "минус",
                "списание",
            } else None
        if sign is None:
            raise HTTPException(
                status_code=400,
                detail="direction: +/−, add/sub, приход/расход",
            )
        sheet_url = (url or "").strip() or (settings.default_stocks_sheet_url or "").strip()
        if not sheet_url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL таблицы в форме или задайте DEFAULT_STOCKS_SHEET_URL в .env",
            )

        loop = asyncio.get_running_loop()

        title_val = (title or "").strip() or None
        comment_val = (comment or "").strip() if comment is not None else ""

        def _run() -> dict:
            return execute_movement_from_sheet(
                inventory_repo,
                movement_repo,
                sign=sign,
                sheet_url=sheet_url,
                source="web",
                title=title_val,
                comment=comment_val,
            )

        try:
            result = await loop.run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка перемещения: {exc}") from exc
        if not result.get("ok"):
            err = str(result.get("error", "unknown"))
            if err == "no_rows":
                raise HTTPException(
                    status_code=400,
                    detail="На листе «movement» нет валидных строк",
                )
            raise HTTPException(status_code=502, detail=f"Ошибка перемещения: {err}")
        return result

    @app.put("/api/stock", dependencies=[Depends(require_login)])
    async def api_put_stock(
        sku: Annotated[str, Form()],
        stock: int = Form(),
    ) -> dict:
        """Задать остаток (как /set_stock). Номенклатура не требуется."""
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        if stock < 0:
            raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным")
        inventory_repo.upsert_stock(sku_n, int(stock))
        return {"sku": sku_n, "stock": int(stock)}

    @app.put("/api/top_flag", dependencies=[Depends(require_login)])
    async def api_put_top_flag(
        sku: Annotated[str, Form()],
        is_top: bool = Form(),
    ) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        try:
            return inventory_repo.set_top_flag_for_sku(sku_n, bool(is_top))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/stock", dependencies=[Depends(require_login)])
    async def api_delete_stock(sku: Annotated[str, Query()]) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        if not inventory_repo.delete_stock_by_sku(sku_n):
            raise HTTPException(status_code=404, detail="Остаток для этого артикула не найден")
        return {"sku": sku_n, "deleted": True}

    @app.delete("/api/nomenclature", dependencies=[Depends(require_login)])
    async def api_delete_nomenclature(sku: Annotated[str, Query()]) -> dict:
        sku_n = sku.strip()
        if not sku_n:
            raise HTTPException(status_code=400, detail="Пустой SKU")
        if not inventory_repo.delete_nomenclature_by_sku(sku_n):
            raise HTTPException(status_code=404, detail="Артикул не найден в номенклатуре")
        return {"sku": sku_n, "deleted": True}

    @app.get("/api/dealer-analysis/files", dependencies=[Depends(require_login)])
    async def api_dealer_analysis_files() -> dict:
        files = dealer_analysis_repo.list_files()
        return {
            "files": [
                {
                    "id": f.id,
                    "file_kind": f.file_kind,
                    "period_label": f.period_label,
                    "run_id": f.run_id,
                    "original_filename": f.original_filename,
                    "file_size": f.file_size,
                    "uploaded_at_ts": f.uploaded_at_ts,
                }
                for f in files
            ]
        }

    @app.get("/api/dealer-analysis/runs", dependencies=[Depends(require_login)])
    async def api_dealer_analysis_runs() -> dict:
        runs = dealer_analysis_repo.list_runs()
        return {
            "runs": [
                {
                    "id": r.id,
                    "period_a_label": r.period_a_label,
                    "period_b_label": r.period_b_label,
                    "source_a_file_id": r.source_a_file_id,
                    "source_b_file_id": r.source_b_file_id,
                    "report_file_id": r.report_file_id,
                    "created_at_ts": r.created_at_ts,
                    "stats": r.stats,
                }
                for r in runs
            ]
        }

    @app.get("/api/dealer-analysis/files/{file_id}/download", dependencies=[Depends(require_login)])
    async def api_dealer_analysis_download(file_id: int) -> Response:
        info = dealer_analysis_repo.get_file(file_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Файл не найден")
        path = dealer_analysis_repo.file_path(file_id)
        if path is None:
            raise HTTPException(status_code=404, detail="Файл на диске не найден")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось прочитать файл: {exc}") from exc
        media = info.mime_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(
            content=content,
            media_type=media,
            headers={
                "Content-Disposition": _content_disposition_attachment(
                    info.original_filename or path.name
                ),
            },
        )

    @app.post("/api/dealer-analysis/analyze", dependencies=[Depends(require_login)])
    async def api_dealer_analysis_analyze(
        period_a_label: Annotated[str, Form()] = "",
        period_b_label: Annotated[str, Form()] = "",
        file_a: UploadFile = File(...),
        file_b: UploadFile = File(...),
    ) -> dict:
        label_a = (period_a_label or "Период A").strip()[:128] or "Период A"
        label_b = (period_b_label or "Период B").strip()[:128] or "Период B"
        data_a = await file_a.read()
        data_b = await file_b.read()
        if len(data_a) > _DEALER_XLSX_MAX_BYTES or len(data_b) > _DEALER_XLSX_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 30 МБ)")
        if not data_a or not data_b:
            raise HTTPException(status_code=400, detail="Оба файла обязательны")
        if not _dealer_xlsx_ok(data_a, file_a.filename or ""):
            raise HTTPException(status_code=400, detail="Первый файл: нужен Excel .xlsx")
        if not _dealer_xlsx_ok(data_b, file_b.filename or ""):
            raise HTTPException(status_code=400, detail="Второй файл: нужен Excel .xlsx")
        try:
            from app.dealer_analysis import run_dealer_analysis

            _rows, stats, report_bytes = await asyncio.to_thread(
                run_dealer_analysis,
                data_a,
                data_b,
                period_a_label=label_a,
                period_b_label=label_b,
            )
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="Не установлен openpyxl: pip install openpyxl",
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Ошибка разбора Excel: {exc}") from exc

        file_a_id = dealer_analysis_repo.store_file(
            file_kind="source_a",
            period_label=label_a,
            original_filename=file_a.filename or "period_a.xlsx",
            content=data_a,
        )
        file_b_id = dealer_analysis_repo.store_file(
            file_kind="source_b",
            period_label=label_b,
            original_filename=file_b.filename or "period_b.xlsx",
            content=data_b,
        )
        report_name = (
            f"dealer_analysis_{_dealer_download_ascii_filename(label_a).removesuffix('.xlsx')}"
            f"_vs_{_dealer_download_ascii_filename(label_b).removesuffix('.xlsx')}.xlsx"
        )
        report_id = dealer_analysis_repo.store_file(
            file_kind="report",
            period_label=f"{label_a} vs {label_b}",
            original_filename=report_name,
            content=report_bytes,
        )
        run_id = dealer_analysis_repo.create_run(
            period_a_label=label_a,
            period_b_label=label_b,
            source_a_file_id=file_a_id,
            source_b_file_id=file_b_id,
            report_file_id=report_id,
            stats=stats,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "report_file_id": report_id,
            "stats": stats,
            "download_url": f"/api/dealer-analysis/files/{report_id}/download",
        }

    @app.get("/api/config/marketplaces", dependencies=[Depends(require_login)])
    async def api_mp_config() -> dict:
        fbs = await asyncio.to_thread(
            describe_fbs_google_sheets,
            default_stocks_sheet_url=settings.default_stocks_sheet_url,
            fbs_list_sheet_url=settings.fbs_list_sheet_url,
            google_service_account_file=settings.google_service_account_file,
            assembly_sheet_name=settings.fbs_assembly_sheet_name,
            assembly_sheet_gid=settings.fbs_assembly_sheet_gid,
            fbs_list_template_sheet=settings.fbs_list_template_sheet,
        )
        return {
            "fbs": fbs,
            "ozon": {
                "configured": is_value_configured(settings.ozon_client_id)
                and is_value_configured(settings.ozon_api_key),
                "warehouse_configured": is_value_configured(settings.ozon_warehouse_id),
            },
            "wildberries": {"configured": is_value_configured(settings.wb_api_token)},
            "yandex_market": {
                "configured": is_value_configured(settings.yandex_campaign_id)
                and is_value_configured(settings.yandex_api_key),
            },
        }

    static_dir = _WEB_ROOT / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
