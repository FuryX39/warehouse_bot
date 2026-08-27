# Warehouse: marketplace reserves and stock push

Ozon, Wildberries and Yandex Market:

- new orders become reserves in the database
- `available = stock - reserve` (never below 0)
- available stock is pushed to marketplaces on a timer

Telegram is **not** part of this loop. It is a separate optional process (`main.py`) for future tasks.

## Processes

| Process | Entry | Role |
|---|---|---|
| Sync | `python run_sync.py` | Reserves + stock push (`RESERVE_INTERVAL_SECONDS`) |
| Web | `python run_web.py` | Panel `/warehouse` (manual sync, catalog, FBS, ship) |
| API | `python run_api.py` | Packing desktop (`/api/v1`) |
| Bot | `python main.py` | Stub. Does not touch stock |

On Windows: `start_sync.bat`, `start_web.bat`, `start_api.bat`.

On the server: `warehouse-sync`, `warehouse-web`, `warehouse-api` (see `deploy/`). Do not run the old `warehouse-bot` unit for stock sync.

## Quick start

1. `python -m venv .venv` and `pip install -r requirements.txt`
2. Copy `.env.example` → `.env` and fill marketplace keys
3. Run `python run_sync.py` plus `python run_web.py`

## Env

- `DB_URL` — default `sqlite:///crm_bot.db`. For PostgreSQL: `postgresql+psycopg://user:pass@127.0.0.1:5432/warehouse` (same value for `MOVEMENT_DB_URL` and `DEALER_ANALYSIS_DB_URL`). Cutover: `python tools/migrate_sqlite_to_postgres.py --dest …`
- `RESERVE_INTERVAL_SECONDS` — pause after each sync cycle (default 120)
- `FULL_SYNC_INTERVAL_SECONDS` — full reserve reconcile per marketplace (default 3600)
- `STOCK_SYNC_ENABLED` — `1` push stocks to MP, `0` only update reserves
- `DEFAULT_STOCKS_SHEET_URL` — optional Google Sheet for stock import in the panel
- Ozon / WB / Yandex keys — see `.env.example`
- `TELEGRAM_BOT_TOKEN` — optional, only for the stub bot

Manual sync, stock edit, sheet import and FBS ship live in `/warehouse` → Маркетплейсы → Синхронизация остатков / FBS.

## DB model

- `product_stocks`: `sku` (PK), `stock`
- `reserves`: `source`, `external_order_id`, `sku`, `quantity`, `status`; unique `(source, external_order_id)`

After a successful **full** fetch, Ozon, Yandex Market and Wildberries reconcile DB reserves with the API snapshot. Cancelled or finished orders drop their reserve rows.

## Project structure

- `run_sync.py` — timer loop
- `app/stock_sync_runner.py` — one cycle + forever loop
- `app/services.py` — `StockCoordinator.sync_cycle`
- `app/repositories.py` — schema and inventory
- `app/adapters/` — Ozon, Wildberries, Yandex Market
- `run_web.py` / `app/web/` — dashboard
- `run_api.py` — packing API
- `main.py` — Telegram stub
