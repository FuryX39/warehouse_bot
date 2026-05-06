import os
from dataclasses import dataclass

from dotenv import load_dotenv


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


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    db_url: str
    reserve_interval_seconds: int = 120
    default_stocks_sheet_url: str = ""
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


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    interval = int(os.getenv("RESERVE_INTERVAL_SECONDS", "120"))
    db_url = os.getenv("DB_URL", "sqlite:///crm_bot.db").strip()
    return Settings(
        telegram_bot_token=token,
        db_url=db_url,
        reserve_interval_seconds=interval,
        default_stocks_sheet_url=os.getenv("DEFAULT_STOCKS_SHEET_URL", "").strip(),
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
    )
