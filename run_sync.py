"""
Точка входа синка маркетплейсов: резервы заказов и пуш доступных остатков.

Отдельно от:
  - run_web.py — браузерная панель
  - run_api.py — десктоп упаковщиков
  - main.py — Telegram (другие задачи, не склад)

Как запускать:
  python run_sync.py

Интервал: RESERVE_INTERVAL_SECONDS. Пуш: STOCK_SYNC_ENABLED.
Ручной запуск того же цикла — в панели /warehouse → Синхронизация остатков.
"""

import logging
import sys
import traceback

from app.bootstrap import create_inventory_stack
from app.stock_sync_runner import run_forever

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    try:
        settings, _inventory_repo, coordinator, _movement_repo, _dealer_repo = create_inventory_stack()
    except Exception as exc:
        logger.error("Не удалось инициализировать синк: %s", exc)
        logger.error(traceback.format_exc())
        sys.exit(1)
    logger.info(
        "Резервы + пуш остатков. Пауза %ss после цикла; полный синк МП не чаще чем раз в %ss.",
        settings.reserve_interval_seconds,
        settings.full_sync_interval_seconds,
    )
    if not settings.stock_sync_enabled:
        logger.warning("STOCK_SYNC_ENABLED=0 — резервы обновляются, пуш остатков на МП выключен.")
    try:
        run_forever(coordinator, settings.reserve_interval_seconds)
    except KeyboardInterrupt:
        logger.info("Stock sync stopped")


if __name__ == "__main__":
    main()
