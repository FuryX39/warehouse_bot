"""Клиент WB Promotion API: кампании, бюджеты и статистика."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Callable

import requests
from requests import exceptions as req_exc

BASE = "https://advert-api.wildberries.ru"
STATUS_LABELS = {
    4: "Готова к запуску",
    7: "Завершена",
    9: "Идут показы",
    11: "Пауза",
}
STATUS_PRESETS: dict[str, tuple[int, ...]] = {
    "active": (9, 11),
    "running": (9,),
    "paused": (11,),
    "finished": (7,),
    "ready": (4,),
    "all": (4, 7, 9, 11),
}
_STATS_STATUSES = frozenset({7, 9, 11})
_BUDGET_STATUSES = frozenset({4, 9, 11})
_RETRIES = 5
_RETRY_BASE = 2.0
_MAX_429_SLEEP = 60.0
_CHUNK = 50


def parse_status_preset(raw: str | None) -> tuple[int, ...]:
    key = str(raw or "active").strip().lower() or "active"
    if key not in STATUS_PRESETS:
        raise ValueError("Неизвестный фильтр статуса кампаний")
    return STATUS_PRESETS[key]


def parse_days(raw: object, *, default: int = 7) -> int:
    try:
        days = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError) as exc:
        raise ValueError("Период задаётся числом дней") from exc
    if days < 1 or days > 31:
        raise ValueError("Период статистики — от 1 до 31 дня")
    return days


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _drr(spend: float, gmv: float) -> float | None:
    if gmv <= 0:
        return None
    return round(100.0 * spend / gmv, 2)


class WbPromotionError(ValueError):
    """Ошибка Promotion API с текстом для пользователя."""


class WbPromotionClient:
    def __init__(
        self,
        token: str,
        *,
        request_fn: Callable[..., requests.Response] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = str(token or "").strip()
        self._request_fn = request_fn or requests.request
        self._sleep = sleep_fn

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise WbPromotionError("Не задан WB_API_TOKEN")
        return {"Authorization": self.token}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = BASE + path
        transient = (
            req_exc.SSLError,
            req_exc.ConnectionError,
            req_exc.ChunkedEncodingError,
            req_exc.Timeout,
        )
        method_u = method.upper()
        last_error = "Ошибка WB Продвижение"
        for attempt in range(1, _RETRIES + 1):
            try:
                response = self._request_fn(
                    method_u,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=60,
                )
            except transient as exc:
                last_error = str(exc)
                if attempt >= _RETRIES:
                    raise WbPromotionError(f"WB Продвижение недоступен: {exc}") from exc
                self._sleep(min(_RETRY_BASE ** (attempt - 1), 30.0))
                continue
            if response.status_code == 429 and attempt < _RETRIES:
                retry_after = 20.0
                raw = (response.headers or {}).get("X-Ratelimit-Retry") or (
                    response.headers or {}
                ).get("Retry-After")
                try:
                    if raw not in (None, ""):
                        retry_after = float(raw)
                except (TypeError, ValueError):
                    pass
                self._sleep(min(max(retry_after, 0.2), _MAX_429_SLEEP))
                continue
            if response.status_code in {401, 403}:
                raise WbPromotionError(
                    "Нет доступа к WB Продвижение. Нужен токен с правом «Продвижение»."
                )
            if response.status_code >= 400:
                body = (response.text or "").strip()
                if len(body) > 400:
                    body = body[:400] + "…"
                raise WbPromotionError(
                    f"WB Продвижение {response.status_code}: {body or response.reason}"
                )
            if not (response.text or "").strip():
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise WbPromotionError("WB Продвижение вернул не JSON") from exc
        raise WbPromotionError(last_error)

    def list_adverts(self, statuses: tuple[int, ...] | None = None) -> list[dict[str, Any]]:
        want = set(statuses or STATUS_PRESETS["all"])
        adverts: list[dict[str, Any]] = []
        nxt = ""
        for _ in range(80):
            params: dict[str, Any] = {
                "limit": _CHUNK,
                "statuses": ",".join(str(code) for code in sorted(want)),
            }
            if nxt:
                params["next"] = nxt
            body = self.request("GET", "/api/advert/v3/adverts", params=params)
            chunk: list[Any] = []
            nxt = ""
            if isinstance(body, dict):
                chunk = body.get("adverts") or []
                nxt = str(body.get("next") or "")
            elif isinstance(body, list):
                chunk = body
            for item in chunk:
                if not isinstance(item, dict):
                    continue
                status = _int(item.get("status"))
                if status in want:
                    adverts.append(item)
            if not nxt:
                break
            self._sleep(0.25)
        return adverts

    def budgets(self, advert_ids: list[int]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        ids = [int(x) for x in advert_ids if int(x) > 0]
        for offset in range(0, len(ids), _CHUNK):
            batch = ids[offset : offset + _CHUNK]
            if offset:
                self._sleep(3.2)
            body = self.request(
                "POST",
                "/api/advert/v2/budget",
                json_body={"advertIds": batch},
            )
            rows = []
            if isinstance(body, dict):
                rows = body.get("adverts") or body.get("budgets") or []
            elif isinstance(body, list):
                rows = body
            for row in rows:
                if not isinstance(row, dict):
                    continue
                advert_id = _int(row.get("advertId") or row.get("id"))
                if advert_id:
                    out[advert_id] = row
        return out

    def fullstats(
        self, advert_ids: list[int], *, begin: date, end: date
    ) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        ids = [int(x) for x in advert_ids if int(x) > 0]
        for offset in range(0, len(ids), _CHUNK):
            batch = ids[offset : offset + _CHUNK]
            if offset:
                self._sleep(21.0)
            body = self.request(
                "GET",
                "/adv/v3/fullstats",
                params={
                    "ids": ",".join(str(x) for x in batch),
                    "beginDate": begin.isoformat(),
                    "endDate": end.isoformat(),
                },
            )
            rows = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                advert_id = _int(row.get("advertId") or row.get("id"))
                if advert_id:
                    out[advert_id] = row
        return out


def _advert_id(item: dict[str, Any]) -> int:
    return _int(item.get("advertId") or item.get("id"))


def _settings(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("settings")
    return raw if isinstance(raw, dict) else {}


def _timestamps(item: dict[str, Any]) -> dict[str, str]:
    raw = item.get("timestamps")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("created", "updated", "started", "ended"):
        value = raw.get(key)
        if value:
            out[key] = str(value)
    return out


def _nms_from_settings(item: dict[str, Any]) -> list[dict[str, Any]]:
    settings = _settings(item)
    rows = settings.get("nms") or item.get("nm_settings") or []
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        nm_id = _int(row.get("nmId") or row.get("nm_id"))
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        bids = row.get("bids") or row.get("bids_kopecks") or {}
        out.append(
            {
                "nm_id": nm_id,
                "name": str(row.get("name") or ""),
                "subject": str(subject.get("name") or ""),
                "bids": bids if isinstance(bids, dict) else {},
            }
        )
    return out


def _placements(item: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(item)
    raw = settings.get("placements")
    return raw if isinstance(raw, dict) else {}


def summarize_stats(row: dict[str, Any] | None) -> dict[str, Any]:
    data = row if isinstance(row, dict) else {}
    spend = _num(data.get("sum"))
    gmv = _num(data.get("sum_price"))
    return {
        "views": _int(data.get("views")),
        "clicks": _int(data.get("clicks")),
        "orders": _int(data.get("orders")),
        "atbs": _int(data.get("atbs")),
        "shks": _int(data.get("shks")),
        "canceled": _int(data.get("canceled")),
        "spend": round(spend, 2),
        "gmv": round(gmv, 2),
        "cpc": round(_num(data.get("cpc")), 2),
        "ctr": round(_num(data.get("ctr")), 2),
        "cr": round(_num(data.get("cr")), 2),
        "drr": _drr(spend, gmv),
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "views": 0,
        "clicks": 0,
        "orders": 0,
        "atbs": 0,
        "shks": 0,
        "canceled": 0,
        "spend": 0.0,
        "gmv": 0.0,
        "cpc": 0.0,
        "ctr": 0.0,
        "cr": 0.0,
        "drr": None,
    }


def _add_stats(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = _empty_stats()
    for key in ("views", "clicks", "orders", "atbs", "shks", "canceled"):
        merged[key] = int(left.get(key) or 0) + int(right.get(key) or 0)
    merged["spend"] = round(float(left.get("spend") or 0) + float(right.get("spend") or 0), 2)
    merged["gmv"] = round(float(left.get("gmv") or 0) + float(right.get("gmv") or 0), 2)
    clicks = merged["clicks"]
    views = merged["views"]
    orders = merged["orders"]
    merged["cpc"] = round(merged["spend"] / clicks, 2) if clicks else 0.0
    merged["ctr"] = round(100.0 * clicks / views, 2) if views else 0.0
    merged["cr"] = round(100.0 * orders / clicks, 2) if clicks else 0.0
    merged["drr"] = _drr(float(merged["spend"]), float(merged["gmv"]))
    return merged


def _day_rows(stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    days = (stats or {}).get("days") if isinstance(stats, dict) else None
    if not isinstance(days, list):
        return []
    out: list[dict[str, Any]] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        item = summarize_stats(day)
        item["date"] = str(day.get("date") or "")[:10]
        out.append(item)
    return out


def _nm_stats(stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    days = (stats or {}).get("days") if isinstance(stats, dict) else None
    if not isinstance(days, list):
        return []
    buckets: dict[int, dict[str, Any]] = {}
    for day in days:
        if not isinstance(day, dict):
            continue
        apps = day.get("apps") if isinstance(day.get("apps"), list) else []
        for app in apps:
            if not isinstance(app, dict):
                continue
            nms = app.get("nms") if isinstance(app.get("nms"), list) else []
            for nm in nms:
                if not isinstance(nm, dict):
                    continue
                nm_id = _int(nm.get("nmId") or nm.get("nm_id"))
                bucket = buckets.setdefault(
                    nm_id,
                    {
                        "nm_id": nm_id,
                        "name": str(nm.get("name") or ""),
                        **_empty_stats(),
                    },
                )
                if nm.get("name") and not bucket.get("name"):
                    bucket["name"] = str(nm.get("name") or "")
                part = summarize_stats(nm)
                merged = _add_stats(bucket, part)
                merged["nm_id"] = nm_id
                merged["name"] = bucket.get("name") or part.get("name") or ""
                buckets[nm_id] = merged
    rows = list(buckets.values())
    rows.sort(key=lambda row: (-float(row.get("spend") or 0), -int(row.get("orders") or 0)))
    return rows


def serialize_campaign(
    item: dict[str, Any],
    *,
    budget: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    settings = _settings(item)
    status = _int(item.get("status"))
    payment = str(
        settings.get("paymentType") or settings.get("payment_type") or item.get("paymentType") or ""
    ).upper()
    payload = {
        "advert_id": _advert_id(item),
        "name": str(settings.get("name") or item.get("name") or "").strip(),
        "status": status,
        "status_label": STATUS_LABELS.get(status, str(status)),
        "payment_type": payment.lower(),
        "budget": round(_num((budget or {}).get("total")), 2) if budget else None,
        "currency": str((budget or {}).get("currency") or "RUB"),
        "stats": summarize_stats(stats) if stats else _empty_stats(),
        "has_stats": bool(stats),
    }
    if include_details:
        payload["timestamps"] = _timestamps(item)
        payload["placements"] = _placements(item)
        payload["nms"] = _nms_from_settings(item)
        payload["days"] = _day_rows(stats)
        payload["nm_stats"] = _nm_stats(stats)
        payload["bid_type"] = str(item.get("bid_type") or settings.get("bid_type") or "")
    return payload


def period_for_days(days: int, *, today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    begin = end - timedelta(days=max(days - 1, 0))
    return begin, end


def list_campaigns(
    client: WbPromotionClient,
    *,
    status: str = "active",
    days: int = 7,
    today: date | None = None,
) -> dict[str, Any]:
    statuses = parse_status_preset(status)
    days = parse_days(days)
    begin, end = period_for_days(days, today=today)
    adverts = client.list_adverts(statuses)
    budget_ids = [
        _advert_id(item)
        for item in adverts
        if _int(item.get("status")) in _BUDGET_STATUSES and _advert_id(item)
    ]
    # На списке статистика только у активных и на паузе: лимит fullstats — 50 id и 3 запроса/мин.
    stats_ids = [
        _advert_id(item)
        for item in adverts
        if _int(item.get("status")) in {9, 11} and _advert_id(item)
    ]
    budgets = client.budgets(budget_ids) if budget_ids else {}
    stats_map = client.fullstats(stats_ids, begin=begin, end=end) if stats_ids else {}
    campaigns = [
        serialize_campaign(
            item,
            budget=budgets.get(_advert_id(item)),
            stats=stats_map.get(_advert_id(item)),
        )
        for item in adverts
    ]
    campaigns.sort(
        key=lambda row: (
            -float((row.get("stats") or {}).get("spend") or 0),
            str(row.get("name") or "").casefold(),
        )
    )
    totals = _empty_stats()
    for row in campaigns:
        totals = _add_stats(totals, row.get("stats") or _empty_stats())
    return {
        "begin": begin.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "status": status,
        "campaigns": campaigns,
        "totals": totals,
        "count": len(campaigns),
    }


def get_campaign(
    client: WbPromotionClient,
    advert_id: int,
    *,
    days: int = 30,
    today: date | None = None,
) -> dict[str, Any]:
    advert_id = int(advert_id)
    if advert_id <= 0:
        raise WbPromotionError("Некорректный ID кампании")
    days = parse_days(days, default=30)
    begin, end = period_for_days(days, today=today)
    body = client.request(
        "GET",
        "/api/advert/v3/adverts",
        params={"advertIds": str(advert_id)},
    )
    rows = []
    if isinstance(body, dict):
        rows = body.get("adverts") or []
    elif isinstance(body, list):
        rows = body
    item = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and _advert_id(row) == advert_id
        ),
        None,
    )
    if item is None:
        raise WbPromotionError("Кампания не найдена")
    status = _int(item.get("status"))
    budgets = (
        client.budgets([advert_id]) if status in _BUDGET_STATUSES else {}
    )
    stats_map = (
        client.fullstats([advert_id], begin=begin, end=end)
        if status in _STATS_STATUSES
        else {}
    )
    campaign = serialize_campaign(
        item,
        budget=budgets.get(advert_id),
        stats=stats_map.get(advert_id),
        include_details=True,
    )
    return {
        "begin": begin.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "campaign": campaign,
    }
