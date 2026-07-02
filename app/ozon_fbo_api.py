"""Сервис Ozon FBO API v2: потребность, черновики, таймслоты, заявки."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.adapters.ozon import OzonAdapter

DRAFT_DELETE_SKU_MODE = 1
API_PAUSE_SEC = 5.0
POLL_PAUSE_MIN_SEC = 1.0
POLL_PAUSE_MAX_SEC = 5.0
CLUSTER_PAUSE_SEC = 2.0
BUNDLE_IDS_CHUNK = 25
SUPPLY_ORDER_GET_CHUNK = 50


def poll_pause_sec(attempt: int) -> float:
    """Пауза перед следующим опросом Ozon (attempt 0 → сразу после 1-го запроса)."""
    if attempt <= 0:
        return POLL_PAUSE_MIN_SEC
    return min(POLL_PAUSE_MAX_SEC, POLL_PAUSE_MIN_SEC + 0.5 * attempt)


def _poll_wait(attempt: int, *, max_attempt: int) -> None:
    if attempt + 1 >= max_attempt:
        return
    time.sleep(poll_pause_sec(attempt))

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
        "warehouse_type": "SORTING_CENTER",
    },
    {
        "warehouse_id": "21957447472000",
        "name": "НОВОСИБИРСК_РФЦ_НОВЫЙ",
        "warehouse_type": "CROSS_DOCK",
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


def macrolocal_cluster_name_map(adapter: OzonAdapter | None) -> dict[str, str]:
    """macrolocal_cluster_id → человекочитаемое название кластера размещения."""
    if adapter is None or not adapter.is_configured():
        return {}
    try:
        return {
            str(int(c["macrolocal_cluster_id"])): str(c.get("name") or "").strip()
            for c in list_macrolocal_clusters(adapter)
            if c.get("macrolocal_cluster_id") is not None and str(c.get("name") or "").strip()
        }
    except Exception:
        return {}


def resolve_macrolocal_cluster_name(
    *,
    cluster_id: str,
    cluster_name: str = "",
    warehouse_name: str = "",
    dropoff_name: str = "",
    name_map: dict[str, str] | None = None,
) -> str:
    """Кластер назначения (размещения), не склад отгрузки и не склад хранения."""
    cid = str(cluster_id or "").strip()
    name = str(cluster_name or "").strip()
    warehouse = str(warehouse_name or "").strip()
    dropoff = str(dropoff_name or "").strip()
    reject = {warehouse, dropoff}
    if name and name not in reject and name != cid and not name.isdigit():
        return name
    if name_map and cid:
        resolved = name_map.get(cid)
        if resolved:
            return resolved
    return ""


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


def draft_dropoff_warehouse_type(
    *,
    warehouse_type: str | None = None,
    name: str | None = None,
) -> str:
    """Тип точки отгрузки для delivery_info.drop_off_warehouse.warehouse_type."""
    explicit = str(warehouse_type or "").strip().upper()
    if explicit in {"SORTING_CENTER", "CROSS_DOCK", "ORDERS_RECEIVING_POINT"}:
        return explicit
    nm = str(name or "").upper()
    if "КРОССДОК" in nm or "CROSSDOCK" in nm:
        return "CROSS_DOCK"
    listed = str(warehouse_type or "").strip().upper()
    if listed == "WAREHOUSE_TYPE_ORDERS_RECEIVING_POINT":
        return "ORDERS_RECEIVING_POINT"
    return "SORTING_CENTER"


def dropoff_presets_from_env() -> list[dict[str, str]]:
    raw = (os.getenv("OZON_FBO_DROPOFF_PRESETS") or "").strip()
    if not raw:
        return [
            {
                "warehouse_id": str(p["warehouse_id"]),
                "name": str(p["name"]),
                "warehouse_type": str(
                    p.get("warehouse_type")
                    or draft_dropoff_warehouse_type(name=str(p.get("name") or ""))
                ),
            }
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
            out.append(
                {
                    "warehouse_id": wid,
                    "name": name,
                    "warehouse_type": str(
                        item.get("warehouse_type")
                        or draft_dropoff_warehouse_type(
                            warehouse_type=item.get("warehouse_type"),
                            name=name,
                        )
                    ),
                }
            )
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
                "draft_warehouse_type": draft_dropoff_warehouse_type(
                    warehouse_type=str(item.get("warehouse_type") or ""),
                    name=str(name),
                ),
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
    dropoff_warehouse_type: str | None = None,
    dropoff_warehouse_name: str | None = None,
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
        wh_type = draft_dropoff_warehouse_type(
            warehouse_type=dropoff_warehouse_type,
            name=dropoff_warehouse_name,
        )
        payload = {
            "cluster_info": cluster_info,
            "delivery_info": {
                "type": "DROPOFF",
                "drop_off_warehouse": {
                    "warehouse_id": int(dropoff_warehouse_id),
                    "warehouse_type": wh_type,
                },
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


def poll_draft_create_info(
    adapter: OzonAdapter,
    draft_id: int,
    *,
    attempts: int = 20,
    delay: float | None = None,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(attempts):
        last = draft_create_info(adapter, draft_id)
        status = str(last.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "ERROR"}:
            return last
        if delay is not None:
            time.sleep(delay)
        else:
            _poll_wait(attempt, max_attempt=attempts)
    return last


def _draft_info_payload(draft_info: dict[str, Any]) -> dict[str, Any]:
    nested = draft_info.get("response")
    if isinstance(nested, dict):
        return nested
    return draft_info


def top_warehouse_from_draft_info(draft_info: dict[str, Any]) -> dict[str, Any] | None:
    data = _draft_info_payload(draft_info)
    for cluster in data.get("clusters") or []:
        supply_type = str(cluster.get("supply_type") or "").upper()
        warehouses = sorted(
            cluster.get("warehouses") or [],
            key=lambda w: int(w.get("total_rank") or 999),
        )
        for wh in warehouses:
            state = str(((wh.get("availability_status") or {}).get("state")) or "").upper()
            if state and state not in {"FULL_AVAILABLE", "AVAILABLE"}:
                continue
            bundle_id = wh.get("bundle_id")
            if not bundle_id:
                continue
            storage_raw = wh.get("storage_warehouse")
            storage = storage_raw if isinstance(storage_raw, dict) else {}
            warehouse_id = storage.get("warehouse_id")
            base: dict[str, Any] = {
                "macrolocal_cluster_id": int(cluster.get("macrolocal_cluster_id") or 0),
                "cluster_name": cluster.get("cluster_name"),
                "bundle_id": str(bundle_id),
                "warehouse_name": storage.get("name") or cluster.get("cluster_name") or "",
                "supply_type": supply_type or None,
            }
            if warehouse_id:
                base["storage_warehouse_id"] = int(warehouse_id)
                return base
            if supply_type == SUPPLY_TYPE_CROSSDOCK:
                return base
    return None


def selected_cluster_warehouse(warehouse: dict[str, Any], delivery_type: str) -> dict[str, Any]:
    sel: dict[str, Any] = {
        "macrolocal_cluster_id": int(warehouse["macrolocal_cluster_id"]),
        "bundle_id": str(warehouse["bundle_id"]),
    }
    if supply_type_for_delivery(delivery_type) != SUPPLY_TYPE_CROSSDOCK:
        wid = warehouse.get("storage_warehouse_id")
        if wid is None:
            raise ValueError("Нет склада назначения для прямой поставки")
        sel["storage_warehouse_id"] = int(wid)
    return sel


def draft_warehouse_error_detail(draft_info: dict[str, Any]) -> str:
    data = _draft_info_payload(draft_info)
    status = str(data.get("status") or "").upper()
    errors = data.get("errors") or []
    if status in {"FAILED", "ERROR"}:
        return f"Черновик Ozon: {status}. {errors}"
    reasons: list[str] = []
    for cluster in data.get("clusters") or []:
        for wh in cluster.get("warehouses") or []:
            avail = wh.get("availability_status") or {}
            state = str(avail.get("state") or "")
            reason = str(avail.get("invalid_reason") or "")
            name = (wh.get("storage_warehouse") or {}).get("name") or cluster.get("cluster_name") or "?"
            if state and state not in {"FULL_AVAILABLE", "AVAILABLE"}:
                reasons.append(f"{name}: {state}" + (f" ({reason})" if reason else ""))
    if reasons:
        return "Нет доступного склада: " + "; ".join(reasons[:5])
    if status == "IN_PROGRESS":
        return "Черновик Ozon ещё обрабатывается — повторите через несколько секунд"
    return "Нет доступного склада в черновике. Проверьте кластер, количество и точку отгрузки."


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
        "selected_cluster_warehouses": [selected_cluster_warehouse(warehouse, delivery_type)],
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
        "selected_cluster_warehouses": [selected_cluster_warehouse(warehouse, delivery_type)],
        "timeslot": {
            "from_in_timezone": timeslot["from_in_timezone"],
            "to_in_timezone": timeslot["to_in_timezone"],
        },
    }
    return _post(adapter, "/v2/draft/supply/create", payload, timeout=180)


def poll_supply_create_status(adapter: OzonAdapter, draft_id: int, *, attempts: int = 20) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(attempts):
        last = _post(adapter, "/v2/draft/supply/create/status", {"draft_id": int(draft_id)}, timeout=120)
        status = str(last.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "ERROR"}:
            return last
        _poll_wait(attempt, max_attempt=attempts)
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

# Статусы «Подготовка» и «Готово к отгрузке» в ЛК Ozon.
SUPPLY_ORDER_PRE_SHIP_STATES = (
    "DATA_FILLING",
    "READY_TO_SUPPLY",
)

SUPPLY_ORDER_STATE_LABELS: dict[str, str] = {
    "DATA_FILLING": "Подготовка",
    "READY_TO_SUPPLY": "Готово к отгрузке",
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
    state_list = [str(s) for s in states if str(s).strip()]
    if not state_list:
        return []
    seen: set[int] = set()
    out: list[int] = []
    body = {
        "filter": {"states": state_list},
        "limit": int(limit),
        "offset": 0,
        "sort_by": "ORDER_CREATION",
        "sort_dir": "DESC",
    }
    try:
        data = _post(adapter, "/v3/supply-order/list", body, timeout=120)
        for raw_id in data.get("order_ids") or []:
            oid = int(raw_id)
            if oid not in seen:
                seen.add(oid)
                out.append(oid)
    except Exception:
        for state in state_list:
            try:
                single = _post(
                    adapter,
                    "/v3/supply-order/list",
                    {
                        "filter": {"states": [state]},
                        "limit": int(limit),
                        "offset": 0,
                        "sort_by": "ORDER_CREATION",
                        "sort_dir": "DESC",
                    },
                    timeout=120,
                )
            except Exception:
                continue
            for raw_id in single.get("order_ids") or []:
                oid = int(raw_id)
                if oid not in seen:
                    seen.add(oid)
                    out.append(oid)
    out.sort(reverse=True)
    return out


def get_supply_orders(adapter: OzonAdapter, order_ids: list[int]) -> list[dict[str, Any]]:
    ids = [int(x) for x in order_ids if int(x) > 0]
    if not ids:
        return []
    orders: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i in range(0, len(ids), SUPPLY_ORDER_GET_CHUNK):
        chunk = ids[i : i + SUPPLY_ORDER_GET_CHUNK]
        if not chunk:
            continue
        data = _post(adapter, "/v3/supply-order/get", {"order_ids": chunk}, timeout=120)
        for order in data.get("orders") or []:
            if not isinstance(order, dict):
                continue
            oid = int(order.get("order_id") or order.get("id") or 0)
            if oid and oid in seen:
                continue
            if oid:
                seen.add(oid)
            orders.append(order)
    return orders


def get_bundle_items(adapter: OzonAdapter, bundle_id: str) -> list[dict[str, Any]]:
    if not bundle_id:
        return []
    return get_bundle_items_map(adapter, [str(bundle_id)]).get(str(bundle_id), [])


def get_bundle_items_map(adapter: OzonAdapter, bundle_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    unique = list(dict.fromkeys(str(b).strip() for b in bundle_ids if str(b).strip()))
    out: dict[str, list[dict[str, Any]]] = {bid: [] for bid in unique}
    if not unique:
        return out
    for i in range(0, len(unique), BUNDLE_IDS_CHUNK):
        chunk = unique[i : i + BUNDLE_IDS_CHUNK]
        try:
            data = _post(
                adapter,
                "/v1/supply-order/bundle",
                {"bundle_ids": chunk, "limit": 100, "offset": 0},
                timeout=120,
            )
        except Exception:
            for bid in chunk:
                try:
                    single = _post(
                        adapter,
                        "/v1/supply-order/bundle",
                        {"bundle_ids": [bid], "limit": 100, "offset": 0},
                        timeout=120,
                    )
                    out[bid] = list(single.get("items") or [])
                except Exception:
                    out[bid] = []
            continue
        items = list(data.get("items") or [])
        if len(chunk) == 1:
            out[chunk[0]] = items
            continue
        for item in items:
            bid = str(item.get("bundle_id") or item.get("bundleId") or "").strip()
            if bid and bid in out:
                out[bid].append(item)
        for bid in chunk:
            if not out[bid]:
                try:
                    single = _post(
                        adapter,
                        "/v1/supply-order/bundle",
                        {"bundle_ids": [bid], "limit": 100, "offset": 0},
                        timeout=120,
                    )
                    out[bid] = list(single.get("items") or [])
                except Exception:
                    out[bid] = []
    return out


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


def normalize_supply_line(order: dict[str, Any], supply: dict[str, Any]) -> dict[str, Any]:
    storage = supply.get("storage_warehouse") or order.get("drop_off_warehouse") or {}
    dropoff = order.get("drop_off_warehouse") or {}
    ts_wrap = order.get("timeslot") or {}
    ts = ts_wrap.get("timeslot") or {}
    tz = (ts_wrap.get("timezone_info") or {}).get("iana_name") or ""
    state = str(supply.get("state") or order.get("state") or "")
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "supply_id": supply.get("supply_id"),
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


def expand_order_supplies(order: dict[str, Any]) -> list[dict[str, Any]]:
    supplies = order.get("supplies") or []
    if not supplies:
        return [normalize_supply_line(order, {})]
    return [normalize_supply_line(order, sup) for sup in supplies]


def normalize_supply_order(order: dict[str, Any]) -> dict[str, Any]:
    lines = expand_order_supplies(order)
    base = dict(lines[0]) if lines else {}
    base["supply_lines"] = lines
    base["supply_count"] = len(lines)
    if len(lines) > 1:
        names = [str(ln.get("warehouse_name") or ln.get("macrolocal_cluster_id") or "?") for ln in lines]
        base["warehouse_name"] = f"{len(lines)} поставок: " + ", ".join(names)
    return base


def _looks_like_inner_supply_id(raw: str) -> bool:
    return bool(raw.isdigit() and len(raw) >= 12)


def inner_supply_id_from_order(
    order: dict[str, Any],
    *,
    bundle_id: str = "",
    warehouse_id: str = "",
) -> int | None:
    supplies = order.get("supplies") or []
    bundle_id = str(bundle_id or "").strip()
    warehouse_id = str(warehouse_id or "").strip()
    if bundle_id:
        for sup in supplies:
            if str(sup.get("bundle_id") or "") == bundle_id:
                sid = sup.get("supply_id")
                return int(sid) if sid is not None else None
    if warehouse_id:
        for sup in supplies:
            storage = sup.get("storage_warehouse") or {}
            wid = str(storage.get("warehouse_id") or sup.get("storage_warehouse_id") or "").strip()
            if wid and wid == warehouse_id:
                sid = sup.get("supply_id")
                return int(sid) if sid is not None else None
    if len(supplies) == 1:
        sid = supplies[0].get("supply_id")
        return int(sid) if sid is not None else None
    return None


def resolve_inner_supply_id(adapter: OzonAdapter, supply: dict[str, Any]) -> int:
    inner_raw = str(supply.get("ozon_supply_id") or "").strip()
    order_raw = str(supply.get("ozon_order_id") or "").strip()
    bundle_id = str(supply.get("ozon_bundle_id") or "").strip()
    warehouse_id = str(supply.get("ozon_warehouse_id") or "").strip()

    if inner_raw and _looks_like_inner_supply_id(inner_raw):
        if not order_raw or inner_raw != order_raw:
            return int(inner_raw)

    order_id = int(order_raw or inner_raw)
    orders = get_supply_orders(adapter, [order_id])
    if not orders:
        raise ValueError(f"Заявка Ozon {order_id} не найдена")
    inner = inner_supply_id_from_order(
        orders[0],
        bundle_id=bundle_id,
        warehouse_id=warehouse_id,
    )
    if inner is None:
        raise ValueError(
            "В заявке Ozon несколько поставок — импортируйте каждую отдельно или укажите bundle_id"
        )
    return int(inner)


def cargo_type_for_supply_kind(supply_kind: str) -> str:
    return "PALLET" if str(supply_kind or "").lower() == "pallet" else "BOX"


def cargo_api_key(cargo: dict[str, Any], idx: int, *, supply_id: int = 0) -> str:
    raw = str(cargo.get("cargo_key") or cargo.get("cargo_number") or "").strip()
    if len(raw) >= 4:
        return raw
    if raw:
        return f"gm-{raw}"
    return f"gm-{supply_id}-{idx + 1}"


def expiration_date_from_ozon(raw: Any) -> str:
    """YYYY-MM-DD для локального хранения из expires_at Ozon."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "T" in s:
        return s.split("T", 1)[0][:10]
    return s[:10]


def cargo_expires_at_for_ozon(item: dict[str, Any]) -> str:
    """ISO datetime для поля expires_at в /v1/cargoes/create."""
    exp = str(item.get("expiration_date") or item.get("expires_at") or "").strip()
    if not exp:
        return ""
    if "T" in exp:
        if exp.endswith("Z") or "+" in exp[10:]:
            return exp
        return f"{exp}Z"
    if len(exp) == 10 and exp[4] == "-" and exp[7] == "-":
        return f"{exp}T00:00:00.000Z"
    return exp


def build_cargoes_create_payload(supply: dict[str, Any], *, inner_supply_id: int) -> dict[str, Any]:
    local_id = int(supply.get("id") or 0)
    cargoes: list[dict[str, Any]] = []
    for idx, cargo in enumerate(supply.get("cargoes") or []):
        items: list[dict[str, Any]] = []
        for item in cargo.get("items") or []:
            offer_id = str(item.get("sku") or item.get("offer_id") or "").strip()
            qty = int(item.get("quantity") or 0)
            if not offer_id or qty <= 0:
                continue
            row: dict[str, Any] = {"offer_id": offer_id, "quantity": qty}
            exp_at = cargo_expires_at_for_ozon(item)
            if exp_at:
                row["expires_at"] = exp_at
            items.append(row)
        if not items:
            continue
        cargo_kind = str(cargo.get("cargo_type") or "").upper()
        if cargo_kind not in {"BOX", "PALLET"}:
            raise ValueError(
                f"Грузоместо #{idx + 1}: укажите тип — короб (BOX) или паллет (PALLET)"
            )
        cargoes.append(
            {
                "key": cargo_api_key(cargo, idx, supply_id=local_id),
                "value": {"type": cargo_kind, "items": items},
            }
        )
    if not cargoes:
        raise ValueError("Добавьте грузоместа и состав")
    return {
        "supply_id": int(inner_supply_id),
        "delete_current_version": True,
        "cargoes": cargoes,
    }


def poll_cargoes_create_info(adapter: OzonAdapter, operation_id: str) -> dict[str, Any]:
    op = str(operation_id or "").strip()
    if not op:
        raise ValueError("Нет operation_id")
    last: dict[str, Any] = {}
    attempts = 25
    for attempt in range(attempts):
        last = adapter.fbo_cargoes_create_info({"operation_id": op})
        status = str(last.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "ERROR"}:
            return last
        _poll_wait(attempt, max_attempt=attempts)
    return last


def fetch_ozon_cargoes(
    adapter: OzonAdapter,
    supply: dict[str, Any],
    *,
    inner_supply_id: int | None = None,
) -> list[dict[str, Any]]:
    """Загрузить грузоместа поставки из Ozon (состав — через bundle каждого ГМ)."""
    sid = int(inner_supply_id) if inner_supply_id is not None else resolve_inner_supply_id(adapter, supply)
    try:
        data = adapter.fbo_cargoes_get([sid])
    except Exception:
        data = _post(adapter, "/v1/cargoes/get", {"supply_ids": [sid]}, timeout=120)
    supplies = list(data.get("supplies") or data.get("supply") or [])
    if not supplies and data.get("supplies_cargoes"):
        supplies = [{"supply_id": sid, "cargoes": []}]
        for block in data.get("supplies_cargoes") or []:
            if int(block.get("supply_id") or 0) == sid:
                supplies[0]["cargoes"] = [
                    {"cargo_id": c.get("cargo_id"), "bundle_id": c.get("bundle_id")}
                    for c in (block.get("cargoes_without_transport_cargoes") or [])
                ]
                break

    out: list[dict[str, Any]] = []
    bundle_ids: list[str] = []
    for sup in supplies:
        for cargo in sup.get("cargoes") or []:
            bundle_id = str(cargo.get("bundle_id") or "").strip()
            if bundle_id:
                bundle_ids.append(bundle_id)
    bundle_map = get_bundle_items_map(adapter, bundle_ids)

    for sup in supplies:
        for idx, cargo in enumerate(sup.get("cargoes") or []):
            cargo_id = str(cargo.get("cargo_id") or "").strip()
            bundle_id = str(cargo.get("bundle_id") or "").strip()
            items: list[dict[str, Any]] = []
            if bundle_id:
                for bi in bundle_map.get(bundle_id, []):
                    offer = str(bi.get("offer_id") or bi.get("sku") or "").strip()
                    qty = int(bi.get("quantity") or 0)
                    if not offer or qty <= 0:
                        continue
                    exp = expiration_date_from_ozon(bi.get("expires_at") or bi.get("expiration_date"))
                    items.append(
                        {
                            "sku": offer,
                            "name": str(bi.get("name") or ""),
                            "quantity": qty,
                            "expiration_date": exp,
                        }
                    )
            if not cargo_id and not items:
                continue
            out.append(
                {
                    "cargo_number": str(idx + 1),
                    "ozon_cargo_id": cargo_id,
                    "cargo_type": str(cargo.get("type") or "").upper(),
                    "comment": "",
                    "items": items,
                }
            )
    return out


def _cargo_ids_for_labels(
    adapter: OzonAdapter,
    supply: dict[str, Any],
    inner_supply_id: int,
) -> list[int]:
    """Актуальные cargo_id из Ozon (локальная БД может быть неполной)."""
    cargo_ids: list[int] = []
    try:
        for cargo in fetch_ozon_cargoes(adapter, supply, inner_supply_id=inner_supply_id):
            cid = str(cargo.get("ozon_cargo_id") or "").strip()
            if cid.isdigit():
                cargo_ids.append(int(cid))
    except Exception:
        pass
    if not cargo_ids:
        for cargo in supply.get("cargoes") or []:
            cid = str(cargo.get("ozon_cargo_id") or "").strip()
            if cid.isdigit():
                cargo_ids.append(int(cid))
    return cargo_ids


def fetch_cargo_labels_pdf(
    adapter: OzonAdapter,
    supply: dict[str, Any],
    *,
    ozon_cargo_ids: list[int] | None = None,
) -> bytes:
    """Запросить этикетки в Ozon и скачать PDF (через file_url из ответа API)."""
    import requests

    inner_id = resolve_inner_supply_id(adapter, supply)
    if ozon_cargo_ids is not None:
        cargo_ids = [int(x) for x in ozon_cargo_ids if int(x) > 0]
    else:
        cargo_ids = _cargo_ids_for_labels(adapter, supply, inner_id)
    payload: dict[str, Any] = {"supply_id": int(inner_id)}
    if cargo_ids:
        payload["cargo_ids"] = cargo_ids
    created = _post(adapter, "/v1/cargoes-label/create", payload, timeout=90)
    operation_id = str(
        created.get("operation_id") or (created.get("result") or {}).get("operation_id") or ""
    ).strip()
    if not operation_id:
        raise ValueError("Ozon не вернул operation_id для этикеток")
    last: dict[str, Any] = {}
    attempts = 30
    for attempt in range(attempts):
        last = _post(adapter, "/v1/cargoes-label/get", {"operation_id": operation_id})
        status = str(last.get("status") or "").upper()
        if status == "SUCCESS":
            result = last.get("result") or {}
            url = str(result.get("file_url") or "").strip()
            if url:
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                return resp.content
            file_guid = str(result.get("file_guid") or "").strip()
            if file_guid:
                try:
                    return adapter.fbo_cargo_labels_file(file_guid)
                except Exception:
                    pass
            raise ValueError("Ozon не вернул ссылку на PDF этикеток")
        if status in {"FAILED", "ERROR"}:
            raise ValueError(f"Ozon не сгенерировал этикетки: {last}")
        _poll_wait(attempt, max_attempt=attempts)
    raise ValueError(f"Таймаут ожидания этикеток Ozon: {last}")


def fetch_batch_cargo_labels_pdfs(
    adapter: OzonAdapter,
    supplies: list[dict[str, Any]],
    *,
    pause_between: float | None = None,
    attempts_per_supply: int = 3,
) -> tuple[list[bytes], list[str]]:
    """Скачать этикетки по списку заявок с паузами и повторами (лимиты Ozon)."""
    pause = CLUSTER_PAUSE_SEC if pause_between is None else pause_between
    pdfs: list[bytes] = []
    errors: list[str] = []
    for idx, supply in enumerate(supplies):
        if idx:
            time.sleep(pause)
        local_id = supply.get("id") or supply.get("ozon_supply_id") or "?"
        last_exc: Exception | None = None
        for attempt in range(attempts_per_supply):
            if attempt:
                time.sleep(pause * (attempt + 1))
            try:
                pdfs.append(fetch_cargo_labels_pdf(adapter, supply))
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                if "429" not in msg and "500" not in msg and "Connection" not in msg:
                    break
        if last_exc is not None:
            errors.append(f"#{local_id}: {last_exc}")
    return pdfs, errors


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
    elif scope in {"pre_ship", "preparing", "preparing_for_shipment"}:
        states = SUPPLY_ORDER_PRE_SHIP_STATES
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
