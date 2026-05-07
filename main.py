import asyncio
import contextlib
import csv
import io
import logging
import secrets
import time
from datetime import datetime, timezone

from telegram import InputFile, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from app.adapters.base import is_value_configured
from app.adapters.ozon import OzonAdapter
from app.adapters.wildberries import WildberriesAdapter
from app.adapters.yandex_market import YandexMarketAdapter
from app.config import load_settings
from app.ozon_analytics import build_ozon_analytics_csv
from app.repositories import InventoryRepository
from app.sheet_import import import_stocks_from_google_sheet
from app.services import StockCoordinator

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _reply_text_resilient(message, text: str, *, max_attempts: int = 4) -> None:
    """Повтор при TimedOut/NetworkError — не блокирует event loop дольше необходимого."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            await message.reply_text(text)
            return
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            logger.warning(
                "Telegram reply_text: сеть/таймаут (попытка %s/%s): %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(3 * attempt, 15))
    if last_exc is not None:
        raise last_exc


CLEAR_DB_PENDING_KEY = "clear_db_pending"
CLEAR_DB_CODE_TTL_SECONDS = 300
SHIP_PENDING_KEY = "ship_pending"
SHIP_CODE_TTL_SECONDS = 300
SHIP_ACTION_TTL_SECONDS = 300

settings = load_settings()
inventory_repo = InventoryRepository(settings.db_url)
inventory_repo.init_schema()

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
)


def _format_sync_result_message(result: dict) -> str:
    text = (
        "Готово. Получено резервов: "
        f"{result['actions_count']}, записано новых: {result['inserted_reservations']}"
    )
    removed = result.get("reconcile_removed", 0) or 0
    updated = result.get("reconcile_updated", 0) or 0
    if removed or updated:
        text += f". Снято устаревших резервов: {removed}, обновлено: {updated}"
    kinds = result.get("adapter_sync_kinds") or {}
    if kinds:
        text += ". МП: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
    sm = result.get("sync_mode")
    if sm:
        text += f" (режим={sm})"
    if result.get("adapter_errors"):
        text += "\n\nПредупреждения:\n- " + "\n- ".join(result["adapter_errors"])
    if result.get("admin_alert"):
        text += "\n\n⚠ Расхождение резервов после полного синка:\n" + result["admin_alert"]
    if len(text) > 4000:
        text = text[:3990] + "\n…(обрезано)"
    return text


async def _send_admin_alert_if_needed(app: Application | None, result: dict) -> None:
    if not result.get("ok") or not result.get("admin_alert") or not settings.telegram_admin_chat_id.strip():
        return
    if app is None:
        return
    try:
        cid = int(settings.telegram_admin_chat_id.strip())
        msg = result["admin_alert"]
        if len(msg) > 4096:
            msg = msg[:4080] + "\n…(обрезано)"
        await app.bot.send_message(chat_id=cid, text=msg)
    except Exception:
        logger.exception("Не удалось отправить TELEGRAM_ADMIN_CHAT_ID уведомление о расхождении WB")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    await update.message.reply_text(
        "Бот запущен. Команды:\n"
        "/status - последний статус синхронизации\n"
        "/sync_now - авто-синк (дельта / полный по FULL_SYNC_INTERVAL_SECONDS на каждый МП)\n"
        "/sync_delta - принудительно дельта (первый запуск без якоря даст полный)\n"
        "/sync_full - полный синк всех МП + сверка; при расхождении — отчёт\n"
        "/set_stock SKU COUNT - задать фактический остаток товара\n"
        "/clear_stock - очистить только общие остатки (без резервов и sync state)\n"
        "/import_sheet URL - импорт остатков из Google Sheets\n"
        "/export_sheet - экспорт остатков в CSV-файл\n"
        "/ozon_analytics [дней] - аналитика Ozon по продажам за период (CSV)\n"
        "/ship_all - отгрузка по всем МП (ready-to-ship, с подтверждением)\n"
        "/ship_ozon - отгрузка только Ozon (ready-to-ship, с подтверждением)\n"
        "/ship_yandex - отгрузка только Yandex Market (ready-to-ship, с подтверждением)\n"
        "/ship_wb - отгрузка только Wildberries (ready-to-ship, с подтверждением)\n"
        "/clear_db - очистка БД (только с подтверждением по коду)"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    snapshots = inventory_repo.get_inventory_snapshot()
    stock_lines = [
        f"{item.sku}: stock={item.stock}, reserve={item.reserve}, available={item.available}"
        for item in snapshots[:10]
    ]

    if coordinator.last_error:
        text = f"Последний запуск с ошибкой: {coordinator.last_error}"
    elif coordinator.last_run_at is None:
        text = "Синхронизация еще не запускалась."
    else:
        text = f"OK. Последний запуск: {coordinator.last_run_at.isoformat()}"
        if coordinator.last_warnings:
            text += "\n\nПредупреждения по интеграциям:\n- " + "\n- ".join(coordinator.last_warnings)
    if stock_lines:
        text = text + "\n\nТекущие остатки:\n" + "\n".join(stock_lines)
    await update.message.reply_text(text)


async def sync_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return
    result = await asyncio.to_thread(coordinator.sync_cycle, "auto")
    if result["ok"]:
        await _reply_text_resilient(update.message, _format_sync_result_message(result))
    else:
        await _reply_text_resilient(update.message, f"Ошибка: {result['error']}")


async def sync_delta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return
    result = await asyncio.to_thread(coordinator.sync_cycle, "delta")
    if result["ok"]:
        await _reply_text_resilient(update.message, _format_sync_result_message(result))
    else:
        await _reply_text_resilient(update.message, f"Ошибка: {result['error']}")


async def sync_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return
    result = await asyncio.to_thread(coordinator.sync_cycle, "full")
    if result["ok"]:
        await _reply_text_resilient(update.message, _format_sync_result_message(result))
    else:
        await _reply_text_resilient(update.message, f"Ошибка: {result['error']}")


async def set_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /set_stock SKU COUNT")
        return

    sku = context.args[0].strip()
    try:
        stock = int(context.args[1])
    except ValueError:
        await update.message.reply_text("COUNT должен быть целым числом.")
        return

    inventory_repo.upsert_stock(sku=sku, stock=stock)
    await update.message.reply_text(f"Сохранено: {sku} stock={max(stock, 0)}")


async def clear_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return
    deleted = inventory_repo.clear_stocks_only()
    await _reply_text_resilient(
        update.message,
        f"Очищены только общие остатки (product_stocks). Удалено строк: {deleted}. "
        "Резервы и sync state не изменены.",
    )


async def import_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) > 1:
        await update.message.reply_text("Использование: /import_sheet [GOOGLE_SHEETS_URL]")
        return
    sheet_url = context.args[0].strip() if context.args else settings.default_stocks_sheet_url
    if not sheet_url:
        await update.message.reply_text(
            "Ссылка не указана. Передайте URL в команде или задайте DEFAULT_STOCKS_SHEET_URL в .env"
        )
        return
    try:
        stocks_by_sku, warnings = import_stocks_from_google_sheet(sheet_url)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Ошибка импорта: {exc}")
        return
    if not stocks_by_sku:
        await update.message.reply_text("Импорт завершен: валидных строк с остатками не найдено.")
        return

    updated = inventory_repo.upsert_stocks(stocks_by_sku)
    text = f"Импорт завершен. Обновлено SKU: {updated}"
    if warnings:
        text += "\n\nПредупреждения:\n- " + "\n- ".join(warnings[:10])
        if len(warnings) > 10:
            text += f"\n... и еще {len(warnings) - 10}"
    await update.message.reply_text(text)


async def ozon_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if not (
        is_value_configured(settings.ozon_client_id) and is_value_configured(settings.ozon_api_key)
    ):
        await _reply_text_resilient(
            update.message,
            "Задайте в .env OZON_CLIENT_ID и OZON_API_KEY.",
        )
        return

    days = settings.ozon_analytics_period_days
    if context.args:
        if len(context.args) != 1:
            await _reply_text_resilient(
                update.message,
                "Использование: /ozon_analytics [ДНЕЙ]\n"
                "Без числа берётся OZON_ANALYTICS_PERIOD_DAYS из .env.",
            )
            return
        try:
            days = int(context.args[0].strip())
        except ValueError:
            await _reply_text_resilient(update.message, "ДНЕЙ должно быть целым числом.")
            return
        days = max(1, min(365, days))

    try:
        csv_bytes, d_from, d_to, fname, row_count = await asyncio.to_thread(
            build_ozon_analytics_csv,
            settings.ozon_client_id,
            settings.ozon_api_key,
            period_days=days,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ozon analytics failed")
        await _reply_text_resilient(update.message, f"Ошибка аналитики Ozon: {exc}")
        return

    caption = (
        f"Ozon analytics (v1/analytics/data): {d_from} — {d_to} ({days} дн.). "
        f"Строк SKU: {row_count}. Метрики: ordered_units, revenue, hits_view_pdp, session_view_pdp."
    )
    if len(caption) > 1024:
        caption = caption[:1021] + "..."
    await update.message.reply_document(
        document=InputFile(io.BytesIO(csv_bytes), filename=fname),
        caption=caption,
    )


async def export_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    snapshots = inventory_repo.get_inventory_snapshot()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    exported_at = datetime.now(timezone.utc).isoformat()
    writer.writerow(["sku", "stock", "reserve", "available", "exported_at_utc"])
    for item in snapshots:
        writer.writerow([item.sku, item.stock, item.reserve, item.available, exported_at])

    filename = f"stocks_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_bytes = output.getvalue().encode("utf-8")
    await update.message.reply_document(
        document=InputFile(io.BytesIO(csv_bytes), filename=filename),
        caption=f"Экспорт завершен. Строк выгружено: {len(snapshots)}",
    )


def _purge_expired_clear_db_pending(bot_data: dict) -> None:
    pending = bot_data.get(CLEAR_DB_PENDING_KEY)
    if not pending:
        return
    now = time.monotonic()
    for chat_id in list(pending.keys()):
        if pending[chat_id]["expires_at"] < now:
            del pending[chat_id]


def _purge_expired_ship_pending(bot_data: dict) -> None:
    pending = bot_data.get(SHIP_PENDING_KEY)
    if not pending:
        return
    now = time.monotonic()
    for chat_id in list(pending.keys()):
        # pending keyed by (chat_id, action)
        for action_key in list(pending[chat_id].keys()):
            if pending[chat_id][action_key]["expires_at"] < now:
                del pending[chat_id][action_key]
        if not pending[chat_id]:
            del pending[chat_id]


async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id
    bot_data = context.application.bot_data
    _purge_expired_clear_db_pending(bot_data)
    pending_map: dict = bot_data.setdefault(CLEAR_DB_PENDING_KEY, {})

    if not context.args:
        code = secrets.token_hex(3).upper()
        pending_map[chat_id] = {
            "code": code,
            "expires_at": time.monotonic() + CLEAR_DB_CODE_TTL_SECONDS,
        }
        await update.message.reply_text(
            "ВНИМАНИЕ: будут удалены все остатки и все резервы в базе. "
            "Восстановить данные нельзя.\n\n"
            "Если вы уверены, в течение 5 минут отправьте команду:\n"
            f"/clear_db {code}\n\n"
            "Любой другой ввод или ожидание — данные не тронут."
        )
        return

    if len(context.args) != 1:
        await update.message.reply_text("Использование: /clear_db  или  /clear_db КОД")
        return

    user_code = context.args[0].strip().upper()
    entry = pending_map.get(chat_id)
    if not entry:
        await update.message.reply_text(
            "Код подтверждения не запрошен. Сначала отправьте /clear_db без параметров."
        )
        return
    if time.monotonic() > entry["expires_at"]:
        del pending_map[chat_id]
        await update.message.reply_text("Код истёк. Отправьте /clear_db снова, чтобы получить новый код.")
        return
    if user_code != entry["code"]:
        await update.message.reply_text("Неверный код. База не изменена.")
        return

    del pending_map[chat_id]
    inventory_repo.clear_all_data()
    coordinator.last_run_at = None
    coordinator.last_error = None
    coordinator.last_warnings = []
    await update.message.reply_text("База очищена: остатки и резервы удалены.")


async def _ship_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, *, sources: set[str], cmd_name: str) -> None:
    chat = update.effective_chat
    if chat is None or update.message is None:
        return
    chat_id = chat.id
    bot_data = context.application.bot_data
    _purge_expired_ship_pending(bot_data)
    pending_map: dict = bot_data.setdefault(SHIP_PENDING_KEY, {})
    chat_pending: dict = pending_map.setdefault(chat_id, {})
    action_key = f"{cmd_name}:{','.join(sorted(sources))}"

    if not context.args:
        code = secrets.token_hex(3).upper()
        chat_pending[action_key] = {
            "code": code,
            "expires_at": time.monotonic() + SHIP_ACTION_TTL_SECONDS,
        }
        await _reply_text_resilient(
            update.message,
            "ВНИМАНИЕ: отгрузка спишет ready-to-ship резервы из остатков и пометит их как shipped.\n"
            "Это необратимо и соответствует факту, что товар физически уехал.\n\n"
            "Если вы уверены, в течение 5 минут отправьте:\n"
            f"/{cmd_name} {code}",
        )
        return

    if len(context.args) != 1:
        await _reply_text_resilient(update.message, f"Использование: /{cmd_name}  или  /{cmd_name} КОД")
        return

    user_code = context.args[0].strip().upper()
    entry = chat_pending.get(action_key)
    if not entry:
        await _reply_text_resilient(
            update.message,
            f"Код подтверждения не запрошен. Сначала отправьте /{cmd_name} без параметров.",
        )
        return
    if time.monotonic() > entry["expires_at"]:
        del chat_pending[action_key]
        await _reply_text_resilient(
            update.message,
            f"Код истёк. Отправьте /{cmd_name} снова, чтобы получить новый код.",
        )
        return
    if user_code != entry["code"]:
        await _reply_text_resilient(update.message, "Неверный код. Отгрузка не выполнена.")
        return

    del chat_pending[action_key]

    # 1) Принудительно синхронизируемся (новые заказы/отмены) перед отгрузкой.
    await _reply_text_resilient(update.message, "Синхронизация перед отгрузкой...")
    sync_result = await asyncio.to_thread(coordinator.sync_cycle)

    # 2) Списываем только "готово к отгрузке" по каждому маркетплейсу.
    ship_stats_by_source: list[str] = []
    total_reserved_units = 0
    total_reserves_shipped = 0
    total_skus = 0

    for adapter in coordinator.adapters:
        if not adapter.is_configured():
            continue
        if not hasattr(adapter, "fetch_ready_to_ship_external_ids"):
            continue
        source = getattr(adapter, "name", "unknown")
        if source not in sources:
            continue

        try:
            active_ids = await asyncio.to_thread(inventory_repo.get_active_reserve_external_ids, source)
            try:
                ready_ids = await asyncio.to_thread(adapter.fetch_ready_to_ship_external_ids, active_ids)
            except TypeError:
                ready_ids = await asyncio.to_thread(adapter.fetch_ready_to_ship_external_ids)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ready-to-ship fetch failed: %s", source)
            ship_stats_by_source.append(f"- {source}: ошибка получения ready-to-ship ({exc})")
            continue

        stats = await asyncio.to_thread(
            inventory_repo.ship_active_reserves_by_external_ids, source, ready_ids
        )
        total_reserved_units += int(stats.get("reserved_units", 0))
        total_reserves_shipped += int(stats.get("reserves_shipped", 0))
        total_skus += int(stats.get("affected_skus", 0))
        ship_stats_by_source.append(
            f"- {source}: shipped reserves={stats['reserves_shipped']}, units={stats['reserved_units']}, skus={stats['affected_skus']}"
        )

    # После списания обновим остатки на маркетплейсах (без повторного fetch заказов).
    def _push_stocks_after_ship() -> None:
        available = inventory_repo.get_available_stock_map()
        for a in coordinator.adapters:
            if a.is_configured():
                a.sync_available_stock(available)

    await asyncio.to_thread(_push_stocks_after_ship)

    coordinator.last_run_at = None
    coordinator.last_error = None
    coordinator.last_warnings = []

    warn_lines = ""
    if sync_result.get("adapter_errors"):
        warn_lines = "\n\nПредупреждения синхронизации:\n- " + "\n- ".join(sync_result["adapter_errors"])

    await _reply_text_resilient(
        update.message,
        f"Отгрузка выполнена ({cmd_name}, только ready-to-ship).\n"
        f"- списано единиц: {total_reserved_units}\n"
        f"- SKU затронуто: {total_skus}\n"
        f"- резервов помечено shipped: {total_reserves_shipped}\n\n"
        "Детализация:\n"
        + "\n".join(ship_stats_by_source)
        + f"{warn_lines}",
    )


async def ship_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ship_impl(update, context, sources={"ozon", "yandex_market", "wildberries"}, cmd_name="ship_all")


async def ship_ozon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ship_impl(update, context, sources={"ozon"}, cmd_name="ship_ozon")


async def ship_yandex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ship_impl(update, context, sources={"yandex_market"}, cmd_name="ship_yandex")


async def ship_wb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ship_impl(update, context, sources={"wildberries"}, cmd_name="ship_wb")


async def scheduled_sync(app: Application | None) -> None:
    result = await asyncio.to_thread(coordinator.sync_cycle, "auto")
    if result["ok"]:
        logger.info(
            "Scheduled sync done. actions=%s, inserted=%s, removed=%s, updated=%s, wb=%s, at=%s",
            result["actions_count"],
            result["inserted_reservations"],
            result.get("reconcile_removed", 0),
            result.get("reconcile_updated", 0),
            result.get("adapter_sync_kinds"),
            result["last_run_at"],
        )
        await _send_admin_alert_if_needed(app, result)
    else:
        logger.error("Scheduled sync failed: %s", result["error"])


async def periodic_sync_loop(interval_seconds: int, app: Application) -> None:
    """
    Периодический синк без наложения запусков: следующий цикл только после завершения предыдущего
    (в отличие от APScheduler interval + max_instances=1, который даёт warning и пропуски).
    """
    while True:
        try:
            await scheduled_sync(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled sync raised unexpectedly")
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env file")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(20.0)
        .get_updates_connect_timeout(60.0)
        .get_updates_read_timeout(120.0)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("sync_now", sync_now))
    app.add_handler(CommandHandler("sync_delta", sync_delta))
    app.add_handler(CommandHandler("sync_full", sync_full))
    app.add_handler(CommandHandler("set_stock", set_stock))
    app.add_handler(CommandHandler("clear_stock", clear_stock))
    app.add_handler(CommandHandler("import_sheet", import_sheet))
    app.add_handler(CommandHandler("export_sheet", export_sheet))
    app.add_handler(CommandHandler("ozon_analytics", ozon_analytics))
    app.add_handler(CommandHandler("ship_all", ship_all))
    app.add_handler(CommandHandler("ship_ozon", ship_ozon))
    app.add_handler(CommandHandler("ship_yandex", ship_yandex))
    app.add_handler(CommandHandler("ship_wb", ship_wb))
    app.add_handler(CommandHandler("clear_db", clear_db))

    logger.info(
        "Sync: пауза %ss после цикла; полный синк каждого МП — не чаще чем раз в %ss (FULL_SYNC_INTERVAL_SECONDS).",
        settings.reserve_interval_seconds,
        settings.full_sync_interval_seconds,
    )

    max_connect_attempts = 5
    for attempt in range(1, max_connect_attempts + 1):
        try:
            await app.initialize()
            break
        except (TimedOut, NetworkError) as exc:
            if attempt >= max_connect_attempts:
                logger.error("Не удалось подключиться к Telegram API после %s попыток", max_connect_attempts)
                raise
            delay = min(5 * attempt, 30)
            logger.warning(
                "Таймаут/сеть Telegram при initialize (попытка %s/%s): %s. Повтор через %s с...",
                attempt,
                max_connect_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    await app.start()

    sync_task = asyncio.create_task(
        periodic_sync_loop(settings.reserve_interval_seconds, app),
        name="periodic_sync",
    )

    await app.updater.start_polling()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sync_task
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
