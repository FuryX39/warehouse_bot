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


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    db_url: str
    movement_db_url: str
    reserve_interval_seconds: int = 120
    default_stocks_sheet_url: str = ""
    fbs_list_sheet_url: str = ""
    fbs_list_template_sheet: str = "FBSTemplate"
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
    # JSON service account для записи листов в Google Таблицу (FBS список и т.п.).
    google_service_account_file: str = ""
    # Поворот PDF-этикеток Ozon (/ozon_labels): 90, -90, 0 = без поворота.
    ozon_label_rotate_degrees: int = 90
    # Формат этикеток Yandex (/yandex_labels): A9_HORIZONTALLY, A9, A7, A4.
    yandex_label_format: str = "A9_HORIZONTALLY"
    # Поворот PDF-этикеток Yandex: 0 = без поворота.
    yandex_label_rotate_degrees: int = 0


def load_settings() -> Settings:
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    interval = int(os.getenv("RESERVE_INTERVAL_SECONDS", "120"))
    db_url = os.getenv("DB_URL", "sqlite:///crm_bot.db").strip()
    movement_db_url = os.getenv("MOVEMENT_DB_URL", "sqlite:///movements.db").strip()
    return Settings(
        telegram_bot_token=token,
        db_url=db_url,
        movement_db_url=movement_db_url,
        reserve_interval_seconds=interval,
        default_stocks_sheet_url=os.getenv("DEFAULT_STOCKS_SHEET_URL", "").strip(),
        fbs_list_sheet_url=os.getenv("FBS_LIST_SHEET_URL", "").strip(),
        fbs_list_template_sheet=os.getenv("FBS_LIST_TEMPLATE_SHEET", "FBSTemplate").strip()
        or "FBSTemplate",
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
        google_service_account_file=_google_service_account_file(),
        ozon_label_rotate_degrees=_ozon_label_rotate_degrees(),
        yandex_label_format=_yandex_label_format(),
        yandex_label_rotate_degrees=_yandex_label_rotate_degrees(),
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


def _google_service_account_file() -> str:
    explicit = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if explicit:
        return explicit
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
