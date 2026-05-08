import asyncio
import contextlib
import csv
import io
import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from telegram import InputFile, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from app.adapters.base import is_value_configured
from app.adapters.ozon import OzonAdapter
from app.adapters.wildberries import WildberriesAdapter
from app.adapters.yandex_market import YandexMarketAdapter
from app.config import load_settings
from app.ozon_analytics import build_ozon_analytics_csv
from app.repositories import (
    AVAILABLE_STOCK_SYNC_KEY,
    InventoryRepository,
    available_stock_map_hash,
)
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
        "/push_stocks - принудительно отправить остатки на все настроенные МП (полный набор SKU из БД, без строки склада = 0)\n"
        "/import_sheet URL - импорт остатков из Google Sheets\n"
        "/export_sheet - экспорт остатков в CSV-файл\n"
        "/ozon_analytics [дней] - аналитика Ozon по продажам за период (CSV)\n"
        "/ship_all - отгрузка по всем МП (WB/Ozon: не new, Yandex: STARTED; с подтверждением)\n"
        "/ship_ozon - отгрузка только Ozon (не new)\n"
        "/ship_yandex - отгрузка только Yandex Market (ожидают сборки / STARTED)\n"
        "/ship_wb - отгрузка только Wildberries (не new)\n"
        "/orders [ОТ] [ДО] - выгрузка заказов из БД в CSV (даты ГГГГ-ММ-ДД или ДД.ММ.ГГГГ, UTC, по first_seen)\n"
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


async def push_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return

    def _run() -> tuple[dict[str, int], list[str]]:
        merged = inventory_repo.build_force_push_available_map()
        errs: list[str] = []
        for adapter in coordinator.adapters:
            if not adapter.is_configured():
                continue
            try:
                adapter.sync_available_stock(merged)
            except Exception as exc:  # noqa: BLE001
                logger.exception("push_stocks: ошибка адаптера %s", adapter.name)
                errs.append(f"{adapter.name}: {exc}")
        inventory_repo.set_sync_int(AVAILABLE_STOCK_SYNC_KEY, available_stock_map_hash(merged))
        return merged, errs

    merged, errs = await asyncio.to_thread(_run)
    lines = [
        "Принудительный полный пуш остатков на все настроенные маркетплейсы.",
        f"SKU в выгрузке: {len(merged)} (склад без строки = 0, минус активные резервы).",
    ]
    if not merged:
        lines.append("В БД нет ни `product_stocks`, ни `order_items` — запросы к API не отправлялись.")
    if errs:
        lines.append("\nОшибки:")
        lines.extend(f"- {e}" for e in errs)
    await _reply_text_resilient(update.message, "\n".join(lines))


def _parse_orders_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _utc_day_start_ts(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def _utc_day_end_ts(d: date) -> int:
    next_day = d + timedelta(days=1)
    return int(datetime.combine(next_day, datetime.min.time(), tzinfo=timezone.utc).timestamp()) - 1


def _format_order_ts(ts: int) -> str:
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args or []
    if len(args) > 2:
        await _reply_text_resilient(
            update.message,
            "Использование: /orders\n"
            "или /orders ОТ_ДАТЫ\n"
            "или /orders ОТ_ДАТЫ ДО_ДАТЫ\n"
            "Формат даты: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (календарный день в UTC, фильтр по first_seen).",
        )
        return

    from_ts: int | None = None
    to_ts: int | None = None
    range_note = "все записи (без фильтра по дате)"
    if len(args) == 1:
        d_from = _parse_orders_date(args[0])
        if d_from is None:
            await _reply_text_resilient(
                update.message,
                f"Не удалось разобрать дату «{args[0]}». Используйте ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.",
            )
            return
        from_ts = _utc_day_start_ts(d_from)
        today_utc = datetime.now(timezone.utc).date()
        to_ts = _utc_day_end_ts(today_utc)
        range_note = f"с {d_from.isoformat()} по {today_utc.isoformat()} (UTC, first_seen)"
    elif len(args) == 2:
        d_from = _parse_orders_date(args[0])
        d_to = _parse_orders_date(args[1])
        if d_from is None or d_to is None:
            await _reply_text_resilient(
                update.message,
                "Не удалось разобрать даты. Используйте ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.",
            )
            return
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        from_ts = _utc_day_start_ts(d_from)
        to_ts = _utc_day_end_ts(d_to)
        range_note = f"{d_from.isoformat()} — {d_to.isoformat()} (UTC, first_seen)"

    rows = await asyncio.to_thread(inventory_repo.list_order_items, from_ts, to_ts)
    if not rows:
        await _reply_text_resilient(update.message, f"Заказов не найдено ({range_note}).")
        return

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        ["source", "external_order_id", "sku", "quantity", "state", "first_seen_utc", "last_seen_utc"]
    )
    for src, ext, sku, qty, state, fst, lst in rows:
        w.writerow(
            [
                src,
                ext,
                sku,
                qty,
                state,
                _format_order_ts(fst),
                _format_order_ts(lst),
            ]
        )
    csv_bytes = buf.getvalue().encode("utf-8")
    fname = f"orders_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    await update.message.reply_document(
        document=InputFile(io.BytesIO(csv_bytes), filename=fname),
        caption=f"Заказы ({range_note}). Строк: {len(rows)}",
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
            "ВНИМАНИЕ: отгрузка выполняется только для заказов в резерве (added), "
            "которые подходят под статусную логику МП.\n"
            "WB/Ozon: не new. Yandex: все, кроме STARTED.\n"
            "Остальные заказы останутся в added.\n"
            "Это необратимо.\n\n"
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

    # 2) Отгружаем только заказы, подходящие под статусные правила по каждому МП:
    # WB/Ozon: не new, Yandex: все added, кроме STARTED.
    ship_stats_by_source: list[str] = []
    total_reserved_units = 0
    total_reserves_shipped = 0
    total_skus = 0
    source_errors: list[str] = []

    def _do_ship_ready() -> tuple[dict[str, dict[str, int]], list[str]]:
        by_source_out: dict[str, dict[str, int]] = {}
        errs: list[str] = []
        adapters_by_name = {a.name: a for a in coordinator.adapters if a.is_configured()}
        for src in sorted(sources):
            adapter = adapters_by_name.get(src)
            if adapter is None:
                continue
            try:
                active_external_ids = inventory_repo.get_active_reserve_external_ids(src)
                if not active_external_ids:
                    by_source_out[src] = {
                        "reserves_shipped": 0,
                        "reserved_units": 0,
                        "affected_skus": 0,
                    }
                    continue

                if isinstance(adapter, WildberriesAdapter):
                    ready_external_ids = adapter.fetch_ready_to_ship_external_ids(active_external_ids)
                    ids_to_ship = set(active_external_ids) & set(ready_external_ids)
                elif isinstance(adapter, OzonAdapter) or isinstance(adapter, YandexMarketAdapter):
                    ready_external_ids = adapter.fetch_ready_to_ship_external_ids()
                    if isinstance(adapter, YandexMarketAdapter):
                        # Для Яндекса команда ship должна исключать STARTED (ожидают сборки).
                        ids_to_ship = set(active_external_ids) - set(ready_external_ids)
                    else:
                        ids_to_ship = set(active_external_ids) & set(ready_external_ids)
                else:
                    ids_to_ship = set()
                stats = inventory_repo.ship_active_reserves_by_external_ids(src, ids_to_ship)
                by_source_out[src] = {
                    "reserves_shipped": int(stats.get("reserves_shipped", 0)),
                    "reserved_units": int(stats.get("reserved_units", 0)),
                    "affected_skus": int(stats.get("affected_skus", 0)),
                }
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{src}: ship failed ({exc})")
                logger.exception("Ship-by-status failed for source=%s", src)
                by_source_out[src] = {
                    "reserves_shipped": 0,
                    "reserved_units": 0,
                    "affected_skus": 0,
                }
        return by_source_out, errs

    by_source, source_errors = await asyncio.to_thread(_do_ship_ready)

    for src in sorted(by_source.keys()):
        st = by_source[src]
        shipped = int(st.get("reserves_shipped", 0))
        units = int(st.get("reserved_units", 0))
        skus = int(st.get("affected_skus", 0))
        total_reserves_shipped += shipped
        total_reserved_units += units
        total_skus += skus
        ship_stats_by_source.append(f"- {src}: shipped={shipped}, units={units}, skus={skus}")
    if not ship_stats_by_source:
        ship_stats_by_source.append("- (нет configured источников для отгрузки)")

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
        f"Отгрузка выполнена ({cmd_name}, по статусам МП).\n"
        f"- списано единиц: {total_reserved_units}\n"
        f"- SKU затронуто: {total_skus}\n"
        f"- резервов помечено shipped: {total_reserves_shipped}\n\n"
        "Детализация:\n"
        + "\n".join(ship_stats_by_source)
        + (
            ("\n\nПредупреждения отгрузки:\n- " + "\n- ".join(source_errors))
            if source_errors
            else ""
        )
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
    app.add_handler(CommandHandler("push_stocks", push_stocks))
    app.add_handler(CommandHandler("orders", orders))
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
