"""
Выгрузка аналитики Ozon через POST /v1/analytics/data (группировка по sku за период).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import requests

OZON_ANALYTICS_URL = "https://api-seller.ozon.ru/v1/analytics/data"

# Метрики по умолчанию для оборачиваемости/спроса. Отдельные метрики в Ozon могут требовать Premium.
DEFAULT_METRICS: tuple[str, ...] = (
    "ordered_units",
    "revenue",
    "hits_view_pdp",
    "session_view_pdp",
)

_MAX_PAGES = 100
_ANALYTICS_REQUEST_TIMEOUT_SEC = 90


def _parse_dimension_sku(dimensions: object) -> tuple[str, str]:
    """Возвращает (ozon_sku_id, offer_id/name) из блока dimensions ответа."""
    if not isinstance(dimensions, list) or not dimensions:
        return "", ""
    first = dimensions[0]
    if not isinstance(first, dict):
        return "", ""
    ozon_id = str(first.get("id", "") or "").strip()
    offer = str(first.get("name", "") or "").strip()
    return ozon_id, offer


def fetch_ozon_analytics_flat_rows(
    client_id: str,
    api_key: str,
    *,
    date_from: str,
    date_to: str,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    limit: int = 1000,
) -> tuple[list[list[object]], tuple[str, ...]]:
    """
    Строки данных: каждая строка —
    offer_id, ozon_sku_id, date_from, date_to, metric1, metric2, ...
    """
    headers = {
        "Client-Id": client_id.strip(),
        "Api-Key": api_key.strip(),
        "Content-Type": "application/json",
    }

    sort_key = "ordered_units" if "ordered_units" in metrics else metrics[0]
    metric_headers = tuple(metrics)

    rows: list[list[object]] = []
    offset = 0

    for _ in range(_MAX_PAGES):
        payload: dict = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": list(metrics),
            "dimension": ["sku"],
            "filters": [],
            "sort": [{"key": sort_key, "order": "DESC"}],
            "limit": limit,
            "offset": offset,
        }
        response = requests.post(
            OZON_ANALYTICS_URL,
            headers=headers,
            json=payload,
            timeout=_ANALYTICS_REQUEST_TIMEOUT_SEC,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            if body:
                raise requests.HTTPError(f"{exc}; body={body}") from exc
            raise

        body = response.json()
        result = body.get("result") or {}
        data = result.get("data") or []
        if not data:
            break

        for item in data:
            if not isinstance(item, dict):
                continue
            dims = item.get("dimensions")
            ozon_sid, offer_id = _parse_dimension_sku(dims)
            m_vals = item.get("metrics")
            if not isinstance(m_vals, list):
                m_vals = []
            row: list[object] = [offer_id, ozon_sid, date_from, date_to]
            for i in range(len(metrics)):
                row.append(m_vals[i] if i < len(m_vals) else "")
            rows.append(row)

        if len(data) < limit:
            break
        offset += limit

    return rows, metric_headers


def build_ozon_analytics_csv(
    client_id: str,
    api_key: str,
    *,
    period_days: int,
    metrics: tuple[str, ...] | None = None,
) -> tuple[bytes, str, str, str, int]:
    """
    CSV UTF-8 с BOM для Excel.
    Возвращает (байты CSV, date_from, date_to, имя файла, число строк с данными без заголовка).
    """
    if period_days < 1:
        period_days = 1
    if period_days > 365:
        period_days = 365

    m = metrics if metrics is not None else DEFAULT_METRICS

    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=period_days - 1)
    date_from = start_d.isoformat()
    date_to = end_d.isoformat()

    flat_rows, metric_names = fetch_ozon_analytics_flat_rows(
        client_id,
        api_key,
        date_from=date_from,
        date_to=date_to,
        metrics=m,
    )

    header = ["offer_id", "ozon_product_id", "period_from", "period_to"] + list(metric_names)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    for r in flat_rows:
        writer.writerow(r)

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    fname = (
        f"ozon_analytics_{date_from}_{date_to}_{datetime.now(timezone.utc).strftime('%H%M%S')}.csv"
    )
    return csv_bytes, date_from, date_to, fname, len(flat_rows)
