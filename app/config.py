import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень репозитория (рядом с run_web.py / main.py), чтобы .env подхватывался независимо от cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _full_sync_interval_seconds() -> int:
    raw = (
        os.getenv("FULL_SYNC_INTERVAL_SECONDS")
        or os.getenv("WB_FULL_SYNC_INTERVAL_SECONDS")
        or "3600"
    ).strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3600
    return max(60, min(86400, n))


def _ozon_analytics_days() -> int:
    raw = os.getenv("OZON_ANALYTICS_PERIOD_DAYS", "30").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 30
    return max(1, min(365, n))


def _web_port() -> int:
    raw = os.getenv("WEB_PORT", "8765").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 8765
    return max(1, min(65535, n))


def _web_dashboard_secret_from_env() -> str:
    """Пароль панели из .env: убираем пробелы и невидимый BOM (частая причина «верный» пароль не подходит)."""
    raw = os.getenv("WEB_DASHBOARD_SECRET", "") or ""
    s = raw.strip()
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff").strip()
    return s


def _stock_sync_enabled() -> bool:
    raw = (os.getenv("STOCK_SYNC_ENABLED", "1") or "1").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    db_url: str
    movement_db_url: str
    reserve_interval_seconds: int = 120
    stock_sync_enabled: bool = True
    default_stocks_sheet_url: str = ""
    fbs_list_sheet_url: str = ""
    fbs_list_template_sheet: str = "FBSTemplate"
    fbs_assembly_sheet_name: str = "assembly"
    fbs_assembly_sheet_gid: int | None = None
    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_warehouse_id: str = ""
    ozon_analytics_period_days: int = 30
    wb_api_token: str = ""
    wb_warehouse_id: str = ""
    # Токен категории «Контент» для POST /content/v2/get/cards/list; если пусто — используется WB_API_TOKEN.
    wb_content_token: str = ""
    yandex_campaign_id: str = ""
    yandex_api_key: str = ""
    # Интервал полного синка (сек) для каждого маркетплейса; между полными — дельта (где поддерживается API).
    full_sync_interval_seconds: int = 3600
    # Опционально: chat_id для алертов о расхождении WB после полного синка (из /start или getUpdates).
    telegram_admin_chat_id: str = ""
    # Веб-панель (run_web.py): пароль для входа в браузер; пустой = веб не запустится.
    web_dashboard_secret: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    # Новая панель /warehouse: первый администратор создаётся при пустой таблице пользователей.
    warehouse_admin_login: str = ""
    warehouse_admin_password: str = ""
    warehouse_admin_display_name: str = "Администратор"
    # JSON service account для записи листов в Google Таблицу (FBS список и т.п.).
    google_service_account_file: str = ""
    # Поворот PDF-этикеток Ozon (/ozon_labels): 90, -90, 0 = без поворота.
    ozon_label_rotate_degrees: int = 90
    # Формат этикеток Yandex (/yandex_labels): A9_HORIZONTALLY, A9, A7, A4.
    yandex_label_format: str = "A9_HORIZONTALLY"
    # Поворот PDF-этикеток Yandex: 0 = без поворота.
    yandex_label_rotate_degrees: int = 0
    # Анализ заказов дилера (/dealer-analysis): отдельная БД и каталог файлов.
    dealer_analysis_db_url: str = ""
    dealer_analysis_data_dir: str = ""
    # Bearer-токен для внешнего API задач (/api/v1/tasks). Пустой = только сессия панели.
    warehouse_tasks_api_token: str = ""


def dealer_analysis_db_url_default() -> str:
    return os.getenv("DEALER_ANALYSIS_DB_URL", "sqlite:///dealer_analysis.db").strip() or "sqlite:///dealer_analysis.db"


def dealer_analysis_data_dir_default() -> Path:
    raw = os.getenv("DEALER_ANALYSIS_DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return _PROJECT_ROOT / "data" / "dealer_analysis"


def resolve_warehouse_admin_credentials(settings: Settings) -> tuple[str, str]:
    """Логин/пароль администратора новой панели из .env.

    WAREHOUSE_ADMIN_PASSWORD может быть не задан — тогда используется WEB_DASHBOARD_SECRET.
    WAREHOUSE_ADMIN_LOGIN по умолчанию — admin.
    """
    login = (settings.warehouse_admin_login or "admin").strip()
    password = (settings.warehouse_admin_password or settings.web_dashboard_secret or "").strip()
    if login.startswith("\ufeff"):
        login = login.lstrip("\ufeff").strip()
    if password.startswith("\ufeff"):
        password = password.lstrip("\ufeff").strip()
    return login, password


def load_settings() -> Settings:
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    interval = int(os.getenv("RESERVE_INTERVAL_SECONDS", "120"))
    db_url = os.getenv("DB_URL", "sqlite:///crm_bot.db").strip() or "sqlite:///crm_bot.db"
    movement_db_url = os.getenv("MOVEMENT_DB_URL", "sqlite:///movements.db").strip() or "sqlite:///movements.db"
    return Settings(
        telegram_bot_token=token,
        db_url=db_url,
        movement_db_url=movement_db_url,
        reserve_interval_seconds=interval,
        stock_sync_enabled=_stock_sync_enabled(),
        default_stocks_sheet_url=os.getenv("DEFAULT_STOCKS_SHEET_URL", "").strip(),
        fbs_list_sheet_url=os.getenv("FBS_LIST_SHEET_URL", "").strip(),
        fbs_list_template_sheet=os.getenv("FBS_LIST_TEMPLATE_SHEET", "FBSTemplate").strip()
        or "FBSTemplate",
        fbs_assembly_sheet_name=os.getenv("FBS_ASSEMBLY_SHEET_NAME", "assembly").strip() or "assembly",
        fbs_assembly_sheet_gid=_fbs_assembly_sheet_gid(),
        ozon_client_id=os.getenv("OZON_CLIENT_ID", "").strip(),
        ozon_api_key=os.getenv("OZON_API_KEY", "").strip(),
        ozon_warehouse_id=os.getenv("OZON_WAREHOUSE_ID", "").strip(),
        ozon_analytics_period_days=_ozon_analytics_days(),
        wb_api_token=os.getenv("WB_API_TOKEN", "").strip(),
        wb_warehouse_id=os.getenv("WB_WAREHOUSE_ID", "").strip(),
        wb_content_token=os.getenv("WB_CONTENT_TOKEN", "").strip(),
        yandex_campaign_id=os.getenv("YANDEX_CAMPAIGN_ID", "").strip(),
        yandex_api_key=os.getenv("YANDEX_API_KEY", "").strip(),
        full_sync_interval_seconds=_full_sync_interval_seconds(),
        telegram_admin_chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip(),
        web_dashboard_secret=_web_dashboard_secret_from_env(),
        web_host=(os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"),
        web_port=_web_port(),
        warehouse_admin_login=os.getenv("WAREHOUSE_ADMIN_LOGIN", "").strip(),
        warehouse_admin_password=os.getenv("WAREHOUSE_ADMIN_PASSWORD", "").strip(),
        warehouse_admin_display_name=(
            os.getenv("WAREHOUSE_ADMIN_DISPLAY_NAME", "Администратор").strip() or "Администратор"
        ),
        google_service_account_file=_google_service_account_file(),
        ozon_label_rotate_degrees=_ozon_label_rotate_degrees(),
        yandex_label_format=_yandex_label_format(),
        yandex_label_rotate_degrees=_yandex_label_rotate_degrees(),
        dealer_analysis_db_url=dealer_analysis_db_url_default(),
        dealer_analysis_data_dir=str(dealer_analysis_data_dir_default()),
        warehouse_tasks_api_token=os.getenv("WAREHOUSE_TASKS_API_TOKEN", "").strip(),
    )


def _yandex_label_format() -> str:
    raw = (os.getenv("YANDEX_LABEL_FORMAT", "A9_HORIZONTALLY") or "A9_HORIZONTALLY").strip()
    allowed = {"A9_HORIZONTALLY", "A9", "A7", "A4"}
    return raw if raw in allowed else "A9_HORIZONTALLY"


def _yandex_label_rotate_degrees() -> int:
    raw = os.getenv("YANDEX_LABEL_ROTATE_DEGREES", "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _ozon_label_rotate_degrees() -> int:
    raw = os.getenv("OZON_LABEL_ROTATE_DEGREES", "90").strip()
    try:
        return int(raw)
    except ValueError:
        return 90


def _fbs_assembly_sheet_gid() -> int | None:
    from app.google_sheet_write import parse_worksheet_gid

    raw = os.getenv("FBS_ASSEMBLY_SHEET_GID", "").strip()
    if raw:
        return parse_worksheet_gid(raw)
    # gid:149721613 в FBS_ASSEMBLY_SHEET_NAME — без отдельной переменной
    name = os.getenv("FBS_ASSEMBLY_SHEET_NAME", "").strip()
    if name.casefold().startswith("gid:"):
        return parse_worksheet_gid(name)
    return None


def _google_service_account_file() -> str:
    explicit = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if explicit:
        return explicit
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
