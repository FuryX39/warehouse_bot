"""
Сборка «склада» для нескольких точек входа.

Зачем отдельный модуль:
  main.py (Telegram) и run_web.py (HTTP) должны использовать ОДИНАКОВЫЕ
  настройки, одну БД и один и тот же список адаптеров. Раньше эта связка
  дублировалась в двух файлах — при изменении адаптера легко забыть
  обновить второе место. Здесь одна функция create_inventory_stack().

Куда смотреть:
  create_inventory_stack() — единственное место, где создаются
  InventoryRepository и StockCoordinator (то, что раньше жило в main.py).
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.ozon import OzonAdapter
from app.adapters.wildberries import WildberriesAdapter
from app.adapters.yandex_market import YandexMarketAdapter
from app.config import Settings, load_settings
from app.config import dealer_analysis_data_dir_default
from app.dealer_analysis_repository import DealerAnalysisRepository
from app.movement_repository import MovementRepository
from app.repositories import InventoryRepository
from app.services import StockCoordinator


def create_inventory_stack() -> tuple[
    Settings,
    InventoryRepository,
    StockCoordinator,
    MovementRepository,
    DealerAnalysisRepository,
]:
    settings = load_settings()
    inventory_repo = InventoryRepository(settings.db_url)
    inventory_repo.init_schema()
    movement_repo = MovementRepository(settings.movement_db_url)
    movement_repo.init_schema()
    dealer_data_dir = dealer_analysis_data_dir_default()
    if settings.dealer_analysis_data_dir:
        dealer_data_dir = Path(settings.dealer_analysis_data_dir)
    dealer_repo = DealerAnalysisRepository(settings.dealer_analysis_db_url, dealer_data_dir)
    dealer_repo.init_schema()
    coordinator = StockCoordinator(
        inventory_repo=inventory_repo,
        adapters=[
            OzonAdapter(
                client_id=settings.ozon_client_id,
                api_key=settings.ozon_api_key,
                warehouse_id=settings.ozon_warehouse_id,
            ),
            WildberriesAdapter(
                api_token=settings.wb_api_token,
                warehouse_id=settings.wb_warehouse_id,
                content_token=settings.wb_content_token,
            ),
            YandexMarketAdapter(
                campaign_id=settings.yandex_campaign_id,
                api_key=settings.yandex_api_key,
            ),
        ],
        full_sync_interval_seconds=settings.full_sync_interval_seconds,
        stock_sync_enabled=settings.stock_sync_enabled,
    )
    return settings, inventory_repo, coordinator, movement_repo, dealer_repo
