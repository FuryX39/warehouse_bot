# Telegram mini-CRM for marketplace stock sync

This bot is a mini-CRM for Ozon, Wildberries and Yandex Market:
- collects new orders as reservations,
- stores stock and reserves in database,
- sends calculated available stock to marketplaces every few minutes.

Core rule:
- `available = stock - reserve`
- if result is negative, the bot sends `0`.

## Quick start

1. Create and activate virtualenv.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy env file:
   - `copy .env.example .env`
4. Fill values in `.env` (see section below).
5. Run:
   - `python main.py`

### One-click start on Windows

You can run bot without opening terminal manually:
- double-click `start_bot.bat`
- or `start_bot_silent.bat` for faster startup

Script does:
- create `.venv` if missing,
- upgrade `pip`,
- install `requirements.txt`,
- run `main.py`.

`start_bot_silent.bat` does minimal checks:
- creates `.venv` only if missing,
- installs dependencies only on first run,
- then just runs `main.py`.

## Where to put API keys

Put keys into `.env`:

- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `DB_URL` - database URL (default sqlite file: `sqlite:///crm_bot.db`)
- `RESERVE_INTERVAL_SECONDS` - sync interval, default 120 sec
- `DEFAULT_STOCKS_SHEET_URL` - optional default Google Sheets URL for `/import_sheet`

- `OZON_CLIENT_ID` - Ozon client id
- `OZON_API_KEY` - Ozon API key
- `OZON_WAREHOUSE_ID` - Ozon warehouse id for stock updates
- `OZON_ANALYTICS_PERIOD_DAYS` - default lookback window for `/ozon_analytics` (1–365 days)

- `WB_API_TOKEN` - Wildberries API token
- `WB_WAREHOUSE_ID` - Wildberries warehouse id for stock updates

- `YANDEX_CAMPAIGN_ID` - Yandex Market campaign id
- `YANDEX_API_KEY` - Yandex Market API key

## DB model

- `product_stocks`:
  - `sku` (PK)
  - `stock` (int)
- `reserves`:
  - `source` (marketplace name)
  - `external_order_id`
  - `sku`
  - `quantity`
  - `status`
  - unique pair: `(source, external_order_id)` for idempotency

After each successful fetch, **Ozon** and **Yandex Market** reconcile DB reserves with the API snapshot: cancelled or finished orders drop their reserve rows. **Wildberries** is not reconciled (only “new orders” endpoint — not a full snapshot).

## Telegram commands

- `/sync_now` - force sync cycle
- `/status` - last run status and inventory snapshot
- `/set_stock SKU COUNT` - set factual stock in DB
- `/import_sheet [URL]` - import stocks from Google Sheets (`URL` optional if default set in `.env`)
- `/export_sheet` - export DB snapshot into CSV file sent by bot
- `/ozon_analytics [days]` - Ozon `POST /v1/analytics/data` grouped by `sku`; CSV (`ordered_units`, `revenue`, etc.)
- `/ship_all` - ship reserves by marketplace statuses (two-step confirmation)
- `/ship_ozon` - ship Ozon reserves where status is not "new" (two-step confirmation)
- `/ship_yandex` - ship Yandex Market reserves in waiting-for-assembly (`PROCESSING` + `STARTED`) (two-step confirmation)
- `/ship_wb` - ship Wildberries reserves where supplier status is not `new` (two-step confirmation)
- `/clear_db` - wipe all stocks and reserves (two-step: request code, then confirm within 5 minutes)

## Ozon analytics CSV

`/ozon_analytics` calls Seller API **`/v1/analytics/data`** for the last `OZON_ANALYTICS_PERIOD_DAYS` (or optional integer argument).

CSV columns:

- `offer_id`, `ozon_product_id`, `period_from`, `period_to`, then metrics (`ordered_units`, `revenue`, `hits_view_pdp`, `session_view_pdp`).

Some Ozon analytics metrics require **seller subscription tiers** — if Ozon rejects a metric, simplify the metric list in `app/ozon_analytics.py` (`DEFAULT_METRICS`).

## Import from Google Sheets

Command format:
- `/import_sheet https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0`
- or just `/import_sheet` if `DEFAULT_STOCKS_SHEET_URL` is set

How parser works:
- reads the sheet named `stocks` via Google CSV endpoint;
- uses first row as header when columns named `sku`/`артикул` and `stock`/`остаток`;
- if no known header found, uses first two columns as `SKU` and `STOCK`.

Notes:
- the sheet must be accessible by link (at least Viewer);
- invalid rows are skipped and returned as warnings in bot response.

## Export to CSV

`/export_sheet` generates CSV file and sends it to chat as document.

Columns:
- `sku`, `stock`, `reserve`, `available`, `exported_at_utc`.

## Clear database

1. Send `/clear_db` — bot replies with a warning and a one-time confirmation code (valid 5 minutes).
2. Send `/clear_db <CODE>` with the exact code to delete all rows in `product_stocks` and `reserves`.

Wrong code, timeout, or tapping `/clear_db` alone again issues a new code and does not delete until confirmed.

## Ship (write-off reserves)

`/ship_all` (or `/ship_ozon`, `/ship_yandex`, `/ship_wb`) is for the moment when you physically ship reserved goods from your warehouse.
Current implementation:
- runs a forced sync cycle first (new orders/cancellations),
- then ships only reserves matching marketplace status rules:
  - Ozon: any of `awaiting_approve`, `awaiting_packaging`, `awaiting_deliver` (i.e. not `awaiting_registration`),
  - Yandex Market: `PROCESSING` + `substatus=STARTED` (waiting for assembly),
  - Wildberries (FBS): any supplier status except `new`.

1. Send `/ship_all` (or one of per-marketplace commands) — bot replies with a warning and a one-time confirmation code (valid 5 minutes).
2. Send `/ship_all <CODE>` (or `/ship_ozon <CODE>`, etc.) to:
   - subtract shipped reserves per SKU from `product_stocks.stock` (never below 0),
   - mark shipped reserve rows in `reserves` as `shipped` (so they won't be re-added).

## Project structure

- `main.py` - bot commands + scheduler
- `app/services.py` - reservation/sync orchestration
- `app/repositories.py` - database schema and inventory queries
- `app/adapters/ozon.py` - Ozon integration
- `app/ozon_analytics.py` - Ozon analytics CSV export helper
- `app/adapters/wildberries.py` - Wildberries integration stub
- `app/adapters/yandex_market.py` - Yandex Market integration stub
