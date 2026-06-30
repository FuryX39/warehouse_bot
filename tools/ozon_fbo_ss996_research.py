#!/usr/bin/env python3
"""Исследование Ozon FBO API для SS996: кластеры, потребность, черновики поставок.

Результаты пишутся в docs/research/ozon_fbo_ss996/.

Примеры:
  python tools/ozon_fbo_ss996_research.py probe
  python tools/ozon_fbo_ss996_research.py create-drafts --clusters 8 --qty 150
  python tools/ozon_fbo_ss996_research.py create-drafts --clusters 8 --qty 150 --create-supplies
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.ozon import OzonAdapter  # noqa: E402
from app.config import load_settings  # noqa: E402

OUT_DIR = ROOT / "docs" / "research" / "ozon_fbo_ss996"
BASE_URL = "https://api-seller.ozon.ru"
DRAFT_DELETE_SKU_MODE = 1  # обязателен для /v1/draft/direct/create (0 = UNSPECIFIED, отклоняется)
API_PAUSE_SEC = 5.0


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _save(name: str, data: Any) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _post(adapter: OzonAdapter, path: str, payload: dict, *, timeout: int = 90) -> dict:
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            return adapter._post_json(path, payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "500" in msg or "10054" in msg or "Connection" in msg:
                time.sleep(API_PAUSE_SEC * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"API failed after retries: {path}: {last_exc}")


def _get(adapter: OzonAdapter, path: str, *, timeout: int = 90) -> bytes:
    return adapter._get_bytes(path, timeout=timeout)


def resolve_offer(adapter: OzonAdapter, offer_id: str) -> dict[str, Any]:
    headers = adapter._headers()
    resp = requests.post(
        f"{BASE_URL}/v3/product/info/list",
        headers=headers,
        json={"offer_id": [offer_id], "product_id": [], "sku": []},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    items = body.get("items") or (body.get("result") or {}).get("items") or []
    if not items:
        raise RuntimeError(f"Товар offer_id={offer_id!r} не найден в Ozon")
    item = items[0]
    pid = item.get("id") or item.get("product_id")
    sku = item.get("sku")
    return {
        "offer_id": offer_id,
        "product_id": int(pid),
        "ozon_sku": int(sku) if sku is not None else None,
        "name": item.get("name"),
        "raw": item,
    }


def fetch_clusters(adapter: OzonAdapter) -> dict[str, Any]:
    return adapter.fbo_cluster_list()


def fetch_analytics_stocks(adapter: OzonAdapter, *, ozon_sku: int) -> dict[str, Any]:
    return _post(adapter, "/v1/analytics/stocks", {"skus": [str(ozon_sku)]}, timeout=120)


def _cluster_name_map(clusters_raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for cluster in clusters_raw.get("clusters") or []:
        name = str(cluster.get("name") or "").strip()
        cid = cluster.get("macrolocal_cluster_id")
        if name and cid is not None:
            out[name] = int(cid)
    return out


def rank_clusters_by_analytics_stocks(
    stocks_body: dict[str, Any],
    clusters_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ранжирование кластеров по ads_cluster из /v1/analytics/stocks."""
    name_to_macro = _cluster_name_map(clusters_raw)
    agg: dict[str, dict[str, Any]] = {}
    for item in stocks_body.get("items") or []:
        name = str(item.get("cluster_name") or "").strip()
        if not name:
            continue
        row = agg.setdefault(
            name,
            {
                "cluster_name": name,
                "cluster_id_analytics": item.get("cluster_id"),
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
    ranked = sorted(
        agg.values(),
        key=lambda r: (-r["ads_cluster"], -r["ads_total"], r["cluster_name"]),
    )
    for row in ranked:
        row["turnover_grades"] = sorted(row["turnover_grades"])
        row["demand_score"] = row["ads_cluster"]
    return ranked


def fetch_manage_stocks(adapter: OzonAdapter, *, ozon_sku: int | None, offer_id: str) -> dict[str, Any]:
    """Потребность/рекомендации по кластерам — POST /v1/analytics/manage/stocks."""
    filter_body: dict[str, Any] = {}
    if ozon_sku:
        filter_body["skus"] = [str(ozon_sku)]
    else:
        filter_body["offer_ids"] = [offer_id]
    payloads = [
        {"filter": {**filter_body, "stock_types": ["FBO"]}},
        {"filter": filter_body},
        {"filter": {**filter_body, "stock_types": ["ALL"]}},
    ]
    errors: list[str] = []
    for payload in payloads:
        try:
            data = _post(adapter, "/v1/analytics/manage/stocks", payload, timeout=120)
            return {"request": payload, "response": data}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{payload}: {exc}")
    return {"errors": errors}


def fetch_placement_zone(adapter: OzonAdapter, ozon_sku: int) -> dict[str, Any]:
    payloads = [
        {"sku": [ozon_sku]},
        {"skus": [ozon_sku]},
    ]
    errors: list[str] = []
    for payload in payloads:
        try:
            data = _post(adapter, "/v1/product/placement-zone/info", payload)
            return {"request": payload, "response": data}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{payload}: {exc}")
    return {"errors": errors}


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_cluster_demand(
    manage_stocks: dict[str, Any],
    clusters_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Собираем строки по кластерам из analytics/manage/stocks + имена из cluster/list."""
    names: dict[str, str] = {}
    ids: dict[str, str] = {}
    for node in _walk(clusters_raw):
        cid = node.get("macrolocal_cluster_id") or node.get("cluster_id") or node.get("id")
        cname = node.get("name") or node.get("cluster_name")
        if cid is not None and cname:
            key = str(cid)
            names[key] = str(cname)
            ids[key] = key

    rows: list[dict[str, Any]] = []
    response = (manage_stocks or {}).get("response") or manage_stocks
    for node in _walk(response):
        cid = (
            node.get("macrolocal_cluster_id")
            or node.get("cluster_id")
            or node.get("macrolocal_cluster")
        )
        if cid is None:
            continue
        key = str(cid)
        demand = _num(
            node.get("recommended_supply")
            or node.get("recommended_quantity")
            or node.get("recommended_count")
            or node.get("need")
            or node.get("demand")
            or node.get("ads")
            or node.get("orders_count")
        )
        if demand is None and not any(
            k in node
            for k in (
                "recommended_supply",
                "recommended_quantity",
                "stock",
                "available",
                "transit",
            )
        ):
            continue
        rows.append(
            {
                "macrolocal_cluster_id": key,
                "cluster_name": names.get(key) or node.get("cluster_name") or node.get("name"),
                "demand_score": demand or 0.0,
                "raw_keys": sorted(node.keys()),
                "raw": node,
            }
        )

    # dedupe by cluster id, keep max demand_score
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row["macrolocal_cluster_id"]
        prev = best.get(cid)
        if prev is None or row["demand_score"] > prev["demand_score"]:
            best[cid] = row
    out = list(best.values())
    out.sort(key=lambda r: (-r["demand_score"], r.get("cluster_name") or "", r["macrolocal_cluster_id"]))
    return out


def pick_top_clusters(demand_rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if demand_rows:
        return demand_rows[:limit]
    return []


def seller_dropoff_warehouse(adapter: OzonAdapter) -> int | None:
    try:
        data = _post(adapter, "/v1/warehouse/fbo/seller/list", {})
    except Exception:
        return None
    for node in _walk(data):
        wid = node.get("warehouse_id") or node.get("drop_off_warehouse_id") or node.get("id")
        if wid is not None:
            try:
                return int(wid)
            except (TypeError, ValueError):
                continue
    return None


def create_direct_draft(adapter: OzonAdapter, *, cluster_id: int, ozon_sku: int, qty: int) -> dict[str, Any]:
    payload = {
        "cluster_info": {
            "macrolocal_cluster_id": cluster_id,
            "items": [{"sku": ozon_sku, "quantity": qty}],
        },
        "deletion_sku_mode": DRAFT_DELETE_SKU_MODE,
    }
    return _post(adapter, "/v1/draft/direct/create", payload, timeout=120)


def create_multi_cluster_draft(
    adapter: OzonAdapter,
    *,
    clusters: list[dict[str, Any]],
    ozon_sku: int,
    qty: int,
    dropoff_warehouse_id: int | None,
) -> dict[str, Any]:
    clusters_info = [
        {
            "macrolocal_cluster_id": int(c["macrolocal_cluster_id"]),
            "items": [{"sku": ozon_sku, "quantity": qty}],
        }
        for c in clusters
    ]
    delivery_info: dict[str, Any] = {"type": "DROPOFF"}
    if dropoff_warehouse_id:
        delivery_info["drop_off_warehouse_id"] = dropoff_warehouse_id
    payload = {
        "clusters_info": clusters_info,
        "delivery_info": delivery_info,
        "deletion_sku_mode": DRAFT_DELETE_SKU_MODE,
    }
    return _post(adapter, "/v1/draft/multi-cluster/create", payload, timeout=180)


def draft_create_info(adapter: OzonAdapter, draft_id: int) -> dict[str, Any]:
    for path, payload in (
        ("/v2/draft/create/info", {"draft_id": draft_id}),
        ("/v1/draft/create/info", {"draft_id": draft_id}),
    ):
        try:
            return {"path": path, "response": _post(adapter, path, {"draft_id": draft_id}, timeout=120)}
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
    raise RuntimeError(f"draft create/info failed: {last}")


def poll_draft_info(adapter: OzonAdapter, draft_id: int, *, attempts: int = 12, delay: float = 5.0) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(attempts):
        last = draft_create_info(adapter, draft_id)
        status = str((last.get("response") or {}).get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "ERROR"}:
            return last
        time.sleep(delay)
    return last


def draft_timeslots(adapter: OzonAdapter, draft_id: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (now + timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payloads = [
        {"draft_id": draft_id, "date_from": date_from, "date_to": date_to},
        {"draft_id": draft_id},
    ]
    for path in ("/v2/draft/timeslot/info", "/v1/draft/timeslot/info"):
        for payload in payloads:
            try:
                return {"path": path, "request": payload, "response": _post(adapter, path, payload, timeout=120)}
            except Exception:
                continue
    return {"error": "timeslot info unavailable"}


def pick_first_timeslot(timeslot_data: dict[str, Any]) -> str | None:
    for node in _walk(timeslot_data.get("response") or timeslot_data):
        for key in ("timeslot_id", "timeslotId", "id"):
            val = node.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _top_warehouse_from_draft_info(draft_info: dict[str, Any]) -> dict[str, Any] | None:
    response = draft_info.get("response") if isinstance(draft_info.get("response"), dict) else draft_info
    for cluster in response.get("clusters") or []:
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


def draft_timeslots_v2(
    adapter: OzonAdapter,
    *,
    draft_id: int,
    warehouse: dict[str, Any],
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    payload = {
        "draft_id": draft_id,
        "date_from": date_from,
        "date_to": date_to,
        "supply_type": "DIRECT",
        "selected_cluster_warehouses": [
            {
                "macrolocal_cluster_id": warehouse["macrolocal_cluster_id"],
                "storage_warehouse_id": warehouse["storage_warehouse_id"],
                "bundle_id": warehouse["bundle_id"],
            }
        ],
    }
    return {
        "path": "/v2/draft/timeslot/info",
        "request": payload,
        "response": _post(adapter, "/v2/draft/timeslot/info", payload, timeout=120),
    }


def pick_timeslot_range(
    timeslot_data: dict[str, Any],
    *,
    date: str,
    hour_from: int,
    hour_to: int,
) -> dict[str, str] | None:
    response = timeslot_data.get("response") or timeslot_data
    result = response.get("result") if isinstance(response.get("result"), dict) else response
    dropoff = result.get("drop_off_warehouse_timeslots") or {}
    days = dropoff.get("days") or []
    target_from = f"{date}T{hour_from:02d}:00:00"
    target_to = f"{date}T{hour_to:02d}:00:00"
    for day in days:
        if str(day.get("date_in_timezone") or "") != date:
            continue
        for slot in day.get("timeslots") or []:
            if slot.get("from_in_timezone") == target_from and slot.get("to_in_timezone") == target_to:
                return {"from_in_timezone": target_from, "to_in_timezone": target_to}
    return None


def create_supply_from_draft_v2(
    adapter: OzonAdapter,
    *,
    draft_id: int,
    warehouse: dict[str, Any],
    timeslot: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "draft_id": draft_id,
        "supply_type": "DIRECT",
        "selected_cluster_warehouses": [
            {
                "macrolocal_cluster_id": warehouse["macrolocal_cluster_id"],
                "storage_warehouse_id": warehouse["storage_warehouse_id"],
                "bundle_id": warehouse["bundle_id"],
            }
        ],
        "timeslot": timeslot,
    }
    return {
        "path": "/v2/draft/supply/create",
        "request": payload,
        "response": _post(adapter, "/v2/draft/supply/create", payload, timeout=180),
    }


def poll_supply_create_status(adapter: OzonAdapter, draft_id: int, *, attempts: int = 20, delay: float = 6.0) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(attempts):
        for path in ("/v2/draft/supply/create/status", "/v1/draft/supply/create/status"):
            try:
                last = {
                    "path": path,
                    "response": _post(adapter, path, {"draft_id": draft_id}, timeout=120),
                }
                break
            except Exception as exc:  # noqa: BLE001
                last = {"error": str(exc)}
        status = str((((last.get("response") or {}).get("status")) or "")).upper()
        if status in {"SUCCESS", "FAILED", "ERROR", "DONE"}:
            return last
        time.sleep(delay)
    return last


def create_supply_from_draft(adapter: OzonAdapter, draft_id: int, timeslot_id: str) -> dict[str, Any]:
    payloads = [
        {"draft_id": draft_id, "timeslot_id": timeslot_id},
        {"draft_id": draft_id, "timeslot": {"timeslot_id": timeslot_id}},
    ]
    for path in ("/v2/draft/supply/create", "/v1/draft/supply/create"):
        for payload in payloads:
            try:
                return {"path": path, "request": payload, "response": _post(adapter, path, payload, timeout=180)}
            except Exception:
                continue
    return {"error": "supply create unavailable"}


def cmd_probe(adapter: OzonAdapter, offer_id: str) -> None:
    tag = _now_tag()
    product = resolve_offer(adapter, offer_id)
    _save(f"{tag}_01_product_{offer_id}.json", product)

    clusters = fetch_clusters(adapter)
    _save(f"{tag}_02_clusters.json", clusters)

    time.sleep(API_PAUSE_SEC)
    stocks = fetch_analytics_stocks(adapter, ozon_sku=int(product["ozon_sku"]))
    _save(f"{tag}_03_analytics_stocks.json", stocks)

    manage = fetch_manage_stocks(adapter, ozon_sku=product.get("ozon_sku"), offer_id=offer_id)
    _save(f"{tag}_04_manage_stocks_obsolete.json", manage)

    placement = {}
    if product.get("ozon_sku"):
        time.sleep(API_PAUSE_SEC)
        placement = fetch_placement_zone(adapter, int(product["ozon_sku"]))
        _save(f"{tag}_05_placement_zone.json", placement)

    demand_rows = rank_clusters_by_analytics_stocks(stocks, clusters)
    if not demand_rows:
        demand_rows = extract_cluster_demand(manage, clusters)
    _save(
        f"{tag}_06_cluster_demand_ranked.json",
        {
            "offer_id": offer_id,
            "product": product,
            "ranked": demand_rows,
            "top8": pick_top_clusters(demand_rows, 8),
        },
    )

    summary = {
        "offer_id": offer_id,
        "product_id": product["product_id"],
        "ozon_sku": product.get("ozon_sku"),
        "analytics_stock_rows": len(stocks.get("items") or []),
        "demand_rows": len(demand_rows),
        "top8": pick_top_clusters(demand_rows, 8),
        "notes": {
            "demand_api": "/v1/analytics/stocks (ads_cluster по кластеру)",
            "obsolete_api": "/v1/analytics/manage/stocks -> obsolete method cannot be used",
            "draft_create": "/v1/draft/direct/create + deletion_sku_mode=1",
            "draft_info": "/v2/draft/create/info",
        },
        "files": sorted(p.name for p in OUT_DIR.glob(f"{tag}_*.json")),
    }
    _save(f"{tag}_00_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_create_drafts(
    adapter: OzonAdapter,
    offer_id: str,
    *,
    clusters_limit: int,
    qty: int,
    mode: str,
    create_supplies: bool,
) -> None:
    tag = _now_tag()
    product = resolve_offer(adapter, offer_id)
    ozon_sku = product.get("ozon_sku")
    if not ozon_sku:
        raise RuntimeError("У товара нет ozon sku в /v3/product/info/list")

    clusters_raw = fetch_clusters(adapter)
    time.sleep(API_PAUSE_SEC)
    stocks = fetch_analytics_stocks(adapter, ozon_sku=int(ozon_sku))
    demand_rows = rank_clusters_by_analytics_stocks(stocks, clusters_raw)
    selected = pick_top_clusters(demand_rows, clusters_limit)

    if len(selected) < clusters_limit:
        # fallback: первые кластеры из справочника
        fallback: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in _walk(clusters_raw):
            cid = node.get("macrolocal_cluster_id") or node.get("cluster_id") or node.get("id")
            if cid is None:
                continue
            key = str(cid)
            if key in seen:
                continue
            seen.add(key)
            fallback.append(
                {
                    "macrolocal_cluster_id": key,
                    "cluster_name": node.get("name") or node.get("cluster_name"),
                    "demand_score": 0.0,
                }
            )
        for row in fallback:
            if len(selected) >= clusters_limit:
                break
            if row["macrolocal_cluster_id"] not in {x["macrolocal_cluster_id"] for x in selected}:
                selected.append(row)

    selected = [r for r in selected if r.get("macrolocal_cluster_id")][:clusters_limit]
    _save(
        f"{tag}_10_selected_clusters.json",
        {"offer_id": offer_id, "qty": qty, "selected": selected, "mode": mode},
    )

    results: list[dict[str, Any]] = []
    dropoff = seller_dropoff_warehouse(adapter)

    if mode == "multi":
        try:
            created = create_multi_cluster_draft(
                adapter,
                clusters=selected,
                ozon_sku=int(ozon_sku),
                qty=qty,
                dropoff_warehouse_id=dropoff,
            )
            draft_id = None
            for node in _walk(created):
                if node.get("draft_id") is not None:
                    draft_id = int(node["draft_id"])
                    break
            info = poll_draft_info(adapter, draft_id) if draft_id else {}
            row = {
                "mode": "multi",
                "clusters": selected,
                "draft_id": draft_id,
                "create_response": created,
                "draft_info": info,
            }
            if create_supplies and draft_id:
                slots = draft_timeslots(adapter, draft_id)
                ts = pick_first_timeslot(slots)
                row["timeslots"] = slots
                if ts:
                    row["supply_create"] = create_supply_from_draft(adapter, draft_id, ts)
            results.append(row)
        except Exception as exc:  # noqa: BLE001
            results.append({"mode": "multi", "error": str(exc), "clusters": selected})
    else:
        for cluster in selected:
            cid = int(cluster["macrolocal_cluster_id"])
            row: dict[str, Any] = {"cluster": cluster, "qty": qty}
            try:
                created = create_direct_draft(adapter, cluster_id=cid, ozon_sku=int(ozon_sku), qty=qty)
                draft_id = None
                for node in _walk(created):
                    if node.get("draft_id") is not None:
                        draft_id = int(node["draft_id"])
                        break
                row["create_response"] = created
                row["draft_id"] = draft_id
                if draft_id:
                    row["draft_info"] = poll_draft_info(adapter, draft_id)
                if create_supplies and draft_id:
                    slots = draft_timeslots(adapter, draft_id)
                    row["timeslots"] = slots
                    ts = pick_first_timeslot(slots)
                    if ts:
                        row["supply_create"] = create_supply_from_draft(adapter, draft_id, ts)
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)
            results.append(row)
            time.sleep(API_PAUSE_SEC)

    out = {
        "offer_id": offer_id,
        "ozon_sku": ozon_sku,
        "product_id": product["product_id"],
        "qty_per_cluster": qty,
        "clusters_requested": clusters_limit,
        "create_supplies": create_supplies,
        "dropoff_warehouse_id": dropoff,
        "results": results,
    }
    path = _save(f"{tag}_11_draft_results.json", out)
    print(f"Saved: {path}")
    print(json.dumps({"drafts": len(results), "errors": sum(1 for r in results if r.get('error'))}, ensure_ascii=False))


def cmd_create_supplies(
    adapter: OzonAdapter,
    *,
    summary_path: Path,
    slot_date: str,
    hour_from: int,
    hour_to: int,
) -> None:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tag = _now_tag()
    results: list[dict[str, Any]] = []
    for cluster in summary.get("clusters") or []:
        draft_id = int(cluster["draft_id"])
        row: dict[str, Any] = {
            "cluster_name": cluster.get("cluster_name"),
            "draft_id": draft_id,
            "macrolocal_cluster_id": cluster.get("macrolocal_cluster_id"),
        }
        try:
            time.sleep(API_PAUSE_SEC)
            info = draft_create_info(adapter, draft_id)
            row["draft_info_status"] = (info.get("response") or {}).get("status")
            warehouse = _top_warehouse_from_draft_info(info)
            if not warehouse:
                row["error"] = "Не найден доступный склад в draft create/info"
                results.append(row)
                continue
            row["warehouse"] = warehouse

            time.sleep(API_PAUSE_SEC)
            slots = draft_timeslots_v2(
                adapter,
                draft_id=draft_id,
                warehouse=warehouse,
                date_from=slot_date,
                date_to=slot_date,
            )
            row["timeslots"] = slots
            picked = pick_timeslot_range(slots, date=slot_date, hour_from=hour_from, hour_to=hour_to)
            if not picked:
                row["error"] = f"Слот {slot_date} {hour_from:02d}:00-{hour_to:02d}:00 недоступен"
                results.append(row)
                continue
            row["timeslot"] = picked

            time.sleep(API_PAUSE_SEC)
            created = create_supply_from_draft_v2(
                adapter,
                draft_id=draft_id,
                warehouse=warehouse,
                timeslot=picked,
            )
            row["supply_create"] = created
            response = created.get("response") or {}
            time.sleep(API_PAUSE_SEC)
            row["supply_create_status"] = poll_supply_create_status(adapter, draft_id)
            status_body = row["supply_create_status"].get("response") or {}
            row["supply_order_id"] = status_body.get("order_id") or status_body.get("supply_order_id")
            row["final_status"] = status_body.get("status")
            if response.get("error_reasons"):
                row["error_reasons"] = response.get("error_reasons")
        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)
        results.append(row)

    out = {
        "source_summary": str(summary_path),
        "slot_date": slot_date,
        "slot_hours": f"{hour_from:02d}:00-{hour_to:02d}:00",
        "results": results,
        "created": sum(1 for r in results if r.get("supply_order_id") or str(r.get("final_status") or "").upper() == "SUCCESS"),
        "errors": sum(1 for r in results if r.get("error")),
    }
    path = _save(f"{tag}_12_supply_create_results.json", out)
    print(f"Saved: {path}")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    load_dotenv(ROOT / ".env")
    settings = load_settings()
    adapter = OzonAdapter(settings.ozon_client_id, settings.ozon_api_key, settings.ozon_warehouse_id)
    if not adapter.is_configured():
        raise SystemExit("Ozon API не настроен в .env")

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_probe = sub.add_parser("probe", help="Собрать кластеры и потребность без создания заявок")
    p_probe.add_argument("--offer-id", default="SS996")
    p_create = sub.add_parser("create-drafts", help="Создать черновики (и опционально заявки) по топ-кластерам")
    p_create.add_argument("--offer-id", default="SS996")
    p_create.add_argument("--clusters", type=int, default=8)
    p_create.add_argument("--qty", type=int, default=150)
    p_create.add_argument("--mode", choices=("direct", "multi"), default="direct")
    p_create.add_argument("--create-supplies", action="store_true")
    p_supplies = sub.add_parser("create-supplies", help="Создать заявки из сохранённых draft_id")
    p_supplies.add_argument(
        "--summary",
        type=Path,
        default=OUT_DIR / "ss996_top8_drafts_summary.json",
        help="JSON со списком draft_id",
    )
    p_supplies.add_argument("--slot-date", default="2026-07-01", help="Дата слота YYYY-MM-DD")
    p_supplies.add_argument("--hour-from", type=int, default=18, help="Час начала слота (локальное время склада)")
    p_supplies.add_argument("--hour-to", type=int, default=19, help="Час окончания слота")
    args = parser.parse_args()

    if args.cmd == "probe":
        cmd_probe(adapter, args.offer_id)
    elif args.cmd == "create-drafts":
        cmd_create_drafts(
            adapter,
            args.offer_id,
            clusters_limit=args.clusters,
            qty=args.qty,
            mode=args.mode,
            create_supplies=args.create_supplies,
        )
    elif args.cmd == "create-supplies":
        cmd_create_supplies(
            adapter,
            summary_path=args.summary,
            slot_date=args.slot_date,
            hour_from=args.hour_from,
            hour_to=args.hour_to,
        )


if __name__ == "__main__":
    main()
