"""Сервис Ozon FBO API v2: потребность, черновики, таймслоты, заявки."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.adapters.ozon import OzonAdapter

DRAFT_DELETE_SKU_MODE = 1
API_PAUSE_SEC = 5.0

DELIVERY_DIRECT = "direct"
DELIVERY_CROSSDOCK = "crossdock"
DELIVERY_TYPES = {DELIVERY_DIRECT, DELIVERY_CROSSDOCK}

SUPPLY_TYPE_DIRECT = "DIRECT"
SUPPLY_TYPE_CROSSDOCK = "CROSSDOCK"

DEMAND_HORIZON_DAYS = 60

DROPOFF_SUPPLY_TYPE_FILTER = ["CREATE_TYPE_CROSSDOCK"]

# Частые точки отгрузки (кросс-док); переопределяется через OZON_FBO_DROPOFF_PRESETS в .env.
DEFAULT_DROPOFF_PRESETS: list[dict[str, Any]] = [
    {
        "warehouse_id": "1020005004424570",
        "name": "НОВОСИБИРСК_ДО_ПЕТУХОВА_БЛОК_3",
    },
    {
        "warehouse_id": "21957447472000",
        "name": "НОВОСИБИРСК_РФЦ_НОВЫЙ",
    },
]


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _post(adapter: OzonAdapter, path: str, payload: dict, *, timeout: int = 120) -> dict:
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            return adapter._post_json(path, payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "500" in msg or "Connection" in msg:
                time.sleep(API_PAUSE_SEC * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Ozon API failed: {path}: {last_exc}")


def resolve_offer_ids(adapter: OzonAdapter, offer_ids: list[str]) -> list[dict[str, Any]]:
    if not offer_ids:
        return []
    body = _post(
        adapter,
        "/v3/product/info/list",
        {"offer_id": offer_ids, "product_id": [], "sku": []},
        timeout=60,
    )
    items = body.get("items") or (body.get("result") or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for item in items:
        oid = str(item.get("offer_id") or "").strip()
        sku = item.get("sku")
        out.append(
            {
                "offer_id": oid,
                "ozon_sku": int(sku) if sku is not None else None,
                "product_id": item.get("id") or item.get("product_id"),
                "name": item.get("name") or oid,
            }
        )
    return out


def fetch_analytics_stocks(adapter: OzonAdapter, *, ozon_skus: list[int]) -> dict[str, Any]:
    if not ozon_skus:
        return {"items": []}
    return _post(adapter, "/v1/analytics/stocks", {"skus": [str(s) for s in ozon_skus]}, timeout=120)


def cluster_name_map(clusters_raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for cluster in clusters_raw.get("clusters") or []:
        name = str(cluster.get("name") or "").strip()
        cid = cluster.get("macrolocal_cluster_id")
        if name and cid is not None:
            out[name] = int(cid)
    return out


def rank_demand_by_clusters(
    stocks_body: dict[str, Any],
    clusters_raw: dict[str, Any],
    *,
    offer_id: str = "",
    ozon_sku: int | None = None,
) -> list[dict[str, Any]]:
    name_to_macro = cluster_name_map(clusters_raw)
    agg: dict[str, dict[str, Any]] = {}
    for item in stocks_body.get("items") or []:
        name = str(item.get("cluster_name") or "").strip()
        if not name:
            continue
        row = agg.setdefault(
            name,
            {
                "cluster_name": name,
                "macrolocal_cluster_id": name_to_macro.get(name),
                "ads_cluster": 0.0,
                "ads_total": 0.0,
                "available_stock_count": 0,
                "turnover_grades": set(),
            },
        )
        row["ads_cluster"] = max(row["ads_cluster"], float(item.get("ads_cluster") or 0))
        row["ads_total"] = max(row["ads_total"], float(item.get("ads") or 0))
        row["available_stock_count"] += int(item.get("available_stock_count") or 0)
        grade = item.get("turnover_grade_cluster") or item.get("turnover_grade")
        if grade:
            row["turnover_grades"].add(str(grade))
    ranked = sorted(agg.values(), key=lambda r: (-r["ads_cluster"], -r["ads_total"], r["cluster_name"]))
    for row in ranked:
        row["turnover_grades"] = sorted(row["turnover_grades"])
        row["offer_id"] = offer_id
        row["ozon_sku"] = ozon_sku
        ads = float(row.get("ads_cluster") or 0)
        row["ads_per_day"] = ads
        row["demand_60_days"] = round(ads * DEMAND_HORIZON_DAYS)
    return ranked


def list_macrolocal_clusters(adapter: OzonAdapter) -> list[dict[str, Any]]:
    raw = adapter.fbo_cluster_list()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for cluster in raw.get("clusters") or []:
        cid = cluster.get("macrolocal_cluster_id")
        if cid is None:
            continue
        macro = int(cid)
        if macro in seen:
            continue
        seen.add(macro)
        out.append(
            {
                "macrolocal_cluster_id": macro,
                "name": str(cluster.get("name") or ""),
                "type": cluster.get("type"),
                "raw": cluster,
            }
        )
    out.sort(key=lambda r: r["name"])
    return out


def dropoff_presets_from_env() -> list[dict[str, str]]:
    raw = (os.getenv("OZON_FBO_DROPOFF_PRESETS") or "").strip()
    if not raw:
        return [
            {"warehouse_id": str(p["warehouse_id"]), "name": str(p["name"])}
            for p in DEFAULT_DROPOFF_PRESETS
        ]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OZON_FBO_DROPOFF_PRESETS: невалидный JSON") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("OZON_FBO_DROPOFF_PRESETS: ожидается непустой JSON-массив")
    out: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        wid = str(item.get("warehouse_id") or item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if wid and name:
            out.append({"warehouse_id": wid, "name": name})
    if not out:
        raise ValueError("OZON_FBO_DROPOFF_PRESETS: нет валидных записей")
    return out


def normalize_dropoff_search(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in data.get("search") or []:
        if not isinstance(item, dict):
            continue
        wid = item.get("warehouse_id") or item.get("id")
        name = item.get("name") or item.get("warehouse_name")
        if wid is None or not name:
            continue
        rows.append(
            {
                "warehouse_id": str(wid),
                "name": str(name),
                "address": str(item.get("address") or ""),
                "warehouse_type": item.get("warehouse_type"),
            }
        )
    return rows


def search_dropoff_warehouses(adapter: OzonAdapter, query: str) -> dict[str, Any]:
    q = str(query or "").strip()
    if len(q) < 4:
        raise ValueError("Введите минимум 4 символа для поиска точки отгрузки")
    payload = {
        "search": q,
        "filter_by_supply_type": DROPOFF_SUPPLY_TYPE_FILTER,
    }
    data = _post(adapter, "/v1/warehouse/fbo/list", payload, timeout=120)
    return {"raw": data, "items": normalize_dropoff_search(data)}


def _draft_items(ozon_sku: int, quantity: int) -> list[dict[str, Any]]:
    return [{"sku": int(ozon_sku), "quantity": int(quantity)}]


def create_draft(
    adapter: OzonAdapter,
    *,
    delivery_type: str,
    macrolocal_cluster_id: int,
    items: list[dict[str, Any]],
    dropoff_warehouse_id: int | None = None,
) -> dict[str, Any]:
    delivery = str(delivery_type or DELIVERY_DIRECT).lower()
    draft_items = []
    for it in items:
        sku = it.get("ozon_sku") or it.get("sku")
        qty = it.get("quantity")
        if sku is None or not qty:
            continue
        draft_items.append({"sku": int(sku), "quantity": int(qty)})
    if not draft_items:
        raise ValueError("Нет товаров для черновика")
    cluster_info = {
        "macrolocal_cluster_id": int(macrolocal_cluster_id),
        "items": draft_items,
    }
    if delivery == DELIVERY_CROSSDOCK:
        if not dropoff_warehouse_id:
            raise ValueError("Для кросс-дока укажите точку отгрузки")
        payload = {
            "cluster_info": cluster_info,
            "delivery_info": {
                "type": "DROPOFF",
                "drop_off_warehouse_id": int(dropoff_warehouse_id),
            },
            "deletion_sku_mode": DRAFT_DELETE_SKU_MODE,
        }
        path = "/v1/draft/crossdock/create"
    else:
        payload = {
            "cluster_info": cluster_info,
            "deletion_sku_mode": DRAFT_DELETE_SKU_MODE,
        }
        path = "/v1/draft/direct/create"
    return {"path": path, "request": payload, "response": _post(adapter, path, payload, timeout=120)}


def draft_create_info(adapter: OzonAdapter, draft_id: int) -> dict[str, Any]:
    return _post(adapter, "/v2/draft/create/info", {"draft_id": int(draft_id)}, timeout=120)


def top_warehouse_from_draft_info(draft_info: dict[str, Any]) -> dict[str, Any] | None:
    for cluster in draft_info.get("clusters") or []:
        warehouses = sorted(
            cluster.get("warehouses") or [],
            key=lambda w: int(w.get("total_rank") or 999),
        )
        for wh in warehouses:
            state = str(((wh.get("availability_status") or {}).get("state")) or "").upper()
            if state and state not in {"FULL_AVAILABLE", "AVAILABLE"}:
                continue
            storage = wh.get("storage_warehouse") or {}
            warehouse_id = storage.get("warehouse_id")
            bundle_id = wh.get("bundle_id")
            if warehouse_id and bundle_id:
                return {
                    "macrolocal_cluster_id": int(cluster.get("macrolocal_cluster_id") or 0),
                    "cluster_name": cluster.get("cluster_name"),
                    "storage_warehouse_id": int(warehouse_id),
                    "bundle_id": str(bundle_id),
                    "warehouse_name": storage.get("name"),
                }
    return None


def supply_type_for_delivery(delivery_type: str) -> str:
    return SUPPLY_TYPE_CROSSDOCK if str(delivery_type).lower() == DELIVERY_CROSSDOCK else SUPPLY_TYPE_DIRECT


def draft_timeslots(
    adapter: OzonAdapter,
    *,
    draft_id: int,
    warehouse: dict[str, Any],
    delivery_type: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    payload = {
        "draft_id": int(draft_id),
        "date_from": date_from,
        "date_to": date_to,
        "supply_type": supply_type_for_delivery(delivery_type),
        "selected_cluster_warehouses": [
            {
                "macrolocal_cluster_id": warehouse["macrolocal_cluster_id"],
                "storage_warehouse_id": warehouse["storage_warehouse_id"],
                "bundle_id": warehouse["bundle_id"],
            }
        ],
    }
    return _post(adapter, "/v2/draft/timeslot/info", payload, timeout=120)


def parse_timeslot_days(timeslot_body: dict[str, Any]) -> list[dict[str, Any]]:
    result = timeslot_body.get("result") if isinstance(timeslot_body.get("result"), dict) else timeslot_body
    dropoff = result.get("drop_off_warehouse_timeslots") or {}
    days = dropoff.get("days") or []
    slots: list[dict[str, Any]] = []
    for day in days:
        date = str(day.get("date_in_timezone") or "")
        for slot in day.get("timeslots") or []:
            slots.append(
                {
                    "date": date,
                    "from_in_timezone": slot.get("from_in_timezone"),
                    "to_in_timezone": slot.get("to_in_timezone"),
                    "label": f"{date} {slot.get('from_in_timezone', '')[11:16]}–{slot.get('to_in_timezone', '')[11:16]}",
                }
            )
    return slots


def create_supply_from_draft(
    adapter: OzonAdapter,
    *,
    draft_id: int,
    warehouse: dict[str, Any],
    delivery_type: str,
    timeslot: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "draft_id": int(draft_id),
        "supply_type": supply_type_for_delivery(delivery_type),
        "selected_cluster_warehouses": [
            {
                "macrolocal_cluster_id": warehouse["macrolocal_cluster_id"],
                "storage_warehouse_id": warehouse["storage_warehouse_id"],
                "bundle_id": warehouse["bundle_id"],
            }
        ],
        "timeslot": {
            "from_in_timezone": timeslot["from_in_timezone"],
            "to_in_timezone": timeslot["to_in_timezone"],
        },
    }
    return _post(adapter, "/v2/draft/supply/create", payload, timeout=180)


def poll_supply_create_status(adapter: OzonAdapter, draft_id: int, *, attempts: int = 15) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(attempts):
        last = _post(adapter, "/v2/draft/supply/create/status", {"draft_id": int(draft_id)}, timeout=120)
        status = str(last.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "ERROR"}:
            return last
        time.sleep(API_PAUSE_SEC)
    return last


def extract_draft_id(create_response: dict[str, Any]) -> int | None:
    for node in _walk(create_response):
        if node.get("draft_id") is not None:
            try:
                return int(node["draft_id"])
            except (TypeError, ValueError):
                continue
    return None


SUPPLY_ORDER_ACTIVE_STATES = (
    "DATA_FILLING",
    "READY_TO_SUPPLY",
    "IN_TRANSIT",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
    "REPORTS_CONFIRMATION_AWAITING",
    "REPORT_REJECTED",
    "REJECTED_AT_SUPPLY_WAREHOUSE",
)

SUPPLY_ORDER_ARCHIVE_STATES = (
    "COMPLETED",
    "CANCELLED",
)

SUPPLY_ORDER_STATE_LABELS: dict[str, str] = {
    "DATA_FILLING": "Заполнение данных",
    "READY_TO_SUPPLY": "Готова к отгрузке",
    "IN_TRANSIT": "В пути",
    "ACCEPTED_AT_SUPPLY_WAREHOUSE": "Принята на точке отгрузки",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE": "Приёмка на складе",
    "REPORTS_CONFIRMATION_AWAITING": "Ожидает подтверждения актов",
    "REPORT_REJECTED": "Акт отклонён",
    "REJECTED_AT_SUPPLY_WAREHOUSE": "Отклонена на точке отгрузки",
    "COMPLETED": "Завершена",
    "CANCELLED": "Отменена",
}


def supply_order_state_label(state: str) -> str:
    return SUPPLY_ORDER_STATE_LABELS.get(str(state or "").strip(), str(state or "—"))


def list_supply_order_ids(
    adapter: OzonAdapter,
    *,
    states: tuple[str, ...] | list[str],
    limit: int = 100,
) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for state in states:
        body = {
            "filter": {"states": [state]},
            "limit": int(limit),
            "offset": 0,
            "sort_by": "ORDER_CREATION",
            "sort_dir": "DESC",
        }
        try:
            data = _post(adapter, "/v3/supply-order/list", body, timeout=120)
        except Exception:
            continue
        for raw_id in data.get("order_ids") or []:
            oid = int(raw_id)
            if oid in seen:
                continue
            seen.add(oid)
            out.append(oid)
        time.sleep(1.5)
    out.sort(reverse=True)
    return out


def get_supply_orders(adapter: OzonAdapter, order_ids: list[int]) -> list[dict[str, Any]]:
    if not order_ids:
        return []
    data = _post(adapter, "/v3/supply-order/get", {"order_ids": [int(x) for x in order_ids]}, timeout=120)
    return list(data.get("orders") or [])


def get_bundle_items(adapter: OzonAdapter, bundle_id: str) -> list[dict[str, Any]]:
    if not bundle_id:
        return []
    data = _post(
        adapter,
        "/v1/supply-order/bundle",
        {"bundle_ids": [str(bundle_id)], "limit": 100, "offset": 0},
        timeout=120,
    )
    return list(data.get("items") or [])


def _format_ozon_datetime(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(s)
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return s[:16].replace("T", " ")


def normalize_supply_order(order: dict[str, Any]) -> dict[str, Any]:
    supplies = order.get("supplies") or []
    supply = supplies[0] if supplies else {}
    storage = supply.get("storage_warehouse") or order.get("drop_off_warehouse") or {}
    dropoff = order.get("drop_off_warehouse") or {}
    ts_wrap = order.get("timeslot") or {}
    ts = ts_wrap.get("timeslot") or {}
    tz = (ts_wrap.get("timezone_info") or {}).get("iana_name") or ""
    state = str(order.get("state") or supply.get("state") or "")
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "state": state,
        "state_label": supply_order_state_label(state),
        "created_date": order.get("created_date"),
        "created_label": _format_ozon_datetime(order.get("created_date")),
        "state_updated_date": order.get("state_updated_date"),
        "warehouse_name": storage.get("name") or dropoff.get("name"),
        "warehouse_id": storage.get("warehouse_id") or dropoff.get("warehouse_id"),
        "macrolocal_cluster_id": supply.get("macrolocal_cluster_id"),
        "bundle_id": supply.get("bundle_id"),
        "is_crossdock": bool(supply.get("is_crossdock")),
        "delivery_type": "crossdock" if supply.get("is_crossdock") else "direct",
        "delivery_type_label": "Кросс-док" if supply.get("is_crossdock") else "Самостоятельно",
        "timeslot_from": ts.get("from"),
        "timeslot_to": ts.get("to"),
        "timeslot_label": (
            f"{_format_ozon_datetime(ts.get('from'))} – {_format_ozon_datetime(ts.get('to'))}"
            + (f" ({tz})" if tz else "")
        ).strip(),
        "data_filling_deadline": order.get("data_filling_deadline"),
    }


def fetch_supply_orders_overview(
    adapter: OzonAdapter,
    *,
    scope: str = "active",
    limit_per_state: int = 100,
) -> dict[str, Any]:
    scope = str(scope or "active").lower()
    if scope == "all":
        states = SUPPLY_ORDER_ACTIVE_STATES + SUPPLY_ORDER_ARCHIVE_STATES
    elif scope == "archive":
        states = SUPPLY_ORDER_ARCHIVE_STATES
    else:
        states = SUPPLY_ORDER_ACTIVE_STATES
    order_ids = list_supply_order_ids(adapter, states=states, limit=limit_per_state)
    orders = get_supply_orders(adapter, order_ids) if order_ids else []
    normalized = [normalize_supply_order(o) for o in orders]
    normalized.sort(key=lambda r: int(r.get("order_id") or 0), reverse=True)
    return {
        "scope": scope,
        "states_queried": list(states),
        "count": len(normalized),
        "orders": normalized,
    }
