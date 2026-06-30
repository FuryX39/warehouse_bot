# Ozon FBO API — исследование SS996

Артефакты для интеграции FBO-поставок в `warehouse_bot`.

## Товар SS996

| Поле | Значение |
|------|----------|
| offer_id | `SS996` |
| product_id | `143590505` |
| ozon_sku (для draft API) | `355923856` |
| placement_zone | `SORT` |

## Рабочие эндпоинты (проверено 2026-06-30)

| Задача | Метод | Примечание |
|--------|-------|------------|
| Список кластеров | `POST /v1/cluster/list` | `macrolocal_cluster_id`, `name` |
| Потребность по кластерам | `POST /v1/analytics/stocks` | `skus: ["355923856"]`, сортировка по `ads_cluster` |
| Зоны размещения | `POST /v1/product/placement-zone/info` | `skus: [355923856]` |
| Черновик прямой поставки | `POST /v1/draft/direct/create` | **`deletion_sku_mode: 1` обязателен** |
| Инфо черновика | `POST /v2/draft/create/info` | склады, `total_score`, `bundle_id` |
| Таймслоты | `POST /v2/draft/timeslot/info` | перед созданием заявки |
| Заявка из черновика | `POST /v2/draft/supply/create` | нужен `timeslot_id` |

## Устаревшие / не работают

- `POST /v1/analytics/manage/stocks` → `obsolete method cannot be used`
- `POST /v1/draft/create` и v1 create/info — миграция на direct/multi-cluster + v2 info
- Без `deletion_sku_mode: 1` → `DeletionSkuMode: value must not be in list [0]`

## Лимиты

- Жёсткий rate limit на draft/analytics — пауза **5+ сек** между запросами.

## Скрипт

```bash
python tools/ozon_fbo_ss996_research.py probe --offer-id SS996
python tools/ozon_fbo_ss996_research.py create-drafts --clusters 8 --qty 150 --mode direct
python tools/ozon_fbo_ss996_research.py create-drafts --clusters 8 --qty 150 --create-supplies
```

JSON-файлы с датой в имени — сырые ответы API.

## Результат теста 2026-06-30

Созданы **8 черновиков** по 150 шт. SS996 (итого 1200 шт.).

Сводка: `ss996_top8_drafts_summary.json`  
Полный ответ API: `20260630_100350_11_draft_results.json`

| # | Кластер | ads_cluster | draft_id | Склад (rank 1) |
|---|---------|-------------|----------|----------------|
| 1 | Москва, МО и Дальние регионы | 4.57 | 116482070 | НОГИНСК_РФЦ |
| 2 | Красноярск | 1.52 | 116482088 | КРАСНОЯРСК_СТАРЦЕВО_РФЦ |
| 3 | Санкт-Петербург и СЗО | 1.43 | 116482138 | СПБ_ШУШАРЫ_РФЦ |
| 4 | Уфа | 1.22 | 116482154 | УФА_РФЦ |
| 5 | Ростов | 1.13 | 116482178 | Ростов_на_Дону_РФЦ |
| 6 | Краснодар | 0.87 | 116482225 | АДЫГЕЙСК_РФЦ |
| 7 | Казань | 0.83 | 116482252 | Казань_РФЦ_НОВЫЙ |
| 8 | Дальний Восток | 0.74 | 116482287 | ХАБАРОВСК_2_РФЦ |

Черновики в Ozon — это ещё **не заявки на поставку**. Для финализации нужен таймслот (`/v2/draft/timeslot/info`) и `POST /v2/draft/supply/create`.

## Результат: заявки созданы 2026-06-30

Слот: **1 июля 2026, 18:00–19:00** (Europe/Moscow).

Сводка: `ss996_top8_supplies_summary.json`  
Полный лог API: `20260630_101553_12_supply_create_results.json`

| # | Кластер | draft_id | supply_order_id | Склад | qty |
|---|---------|----------|-----------------|-------|-----|
| 1 | Москва, МО и Дальние регионы | 116482070 | **114315728** | НОГИНСК_РФЦ | 150 |
| 2 | Красноярск | 116482088 | **114315746** | КРАСНОЯРСК_СТАРЦЕВО_РФЦ | 150 |
| 3 | Санкт-Петербург и СЗО | 116482138 | **114315787** | СПБ_ШУШАРЫ_РФЦ | 150 |
| 4 | Уфа | 116482154 | **114315824** | УФА_РФЦ | 150 |
| 5 | Ростов | 116482178 | **114315867** | Ростов_на_Дону_РФЦ | 150 |
| 6 | Краснодар | 116482225 | **114315915** | АДЫГЕЙСК_РФЦ | 150 |
| 7 | Казань | 116482252 | **114315945** | Казань_РФЦ_НОВЫЙ | 150 |
| 8 | Дальний Восток | 116482287 | **114315960** | ХАБАРОВСК_2_РФЦ | 150 |

```bash
python tools/ozon_fbo_ss996_research.py create-supplies --slot-date 2026-07-01 --hour-from 18 --hour-to 19
```

1. Расширить `OzonAdapter`: `analytics_stocks`, `draft_direct_create`, `draft_create_info_v2`, `deletion_sku_mode`.
2. Ранжирование кластеров: `rank_clusters_by_analytics_stocks()` из скрипта.
3. UI FBO: кнопка «распределить по топ-N кластерам» + создание черновиков.
4. Маппинг `cluster_name` (analytics) ↔ `macrolocal_cluster_id` (cluster/list).
