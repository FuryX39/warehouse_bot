"""Read-only клиент FBY (FBO YM) Partner API: заявки, товары, CARGO_UNITS."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import requests
from pypdf import PdfReader

from app.adapters.base import is_value_configured
from app.config import Settings

YANDEX_PARTNER_BASE = "https://api.partner.market.yandex.ru"
FBY_PLACEMENT = "FBY"
CHILD_SUBTYPE_MARKERS = ("CHILD",)
HIDDEN_SUPPLY_STATUSES = frozenset(
    {
        "CANCELLED",
        "REJECTED",
        "DELETED",
        "CANCELLED_BY_MARKETPLACE",
        "CANCELLED_BY_PARTNER",
    }
)
CARGO_CODE_RE = re.compile(r"B[0-9A-F]{10,}", re.IGNORECASE)
_CANCELLED_DOC = re.compile(r"cancel|reject|delet", re.I)


@dataclass(frozen=True)
class CargoUnitPage:
    page_index: int
    cargo_code: str


def parse_cargo_units_pdf(pdf_bytes: bytes) -> list[CargoUnitPage]:
    """Разобрать CARGO_UNITS постранично: на каждой странице один код B…."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("PDF грузомест ещё недоступен")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ValueError("Не удалось прочитать PDF грузомест") from exc
    if not reader.pages:
        raise ValueError("PDF грузомест пустой")
    seen: set[str] = set()
    out: list[CargoUnitPage] = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        found = ""
        for match in CARGO_CODE_RE.findall(text):
            code = str(match).strip().upper()
            if code in seen:
                continue
            found = code
            break
        if not found:
            raise ValueError(f"На странице {index + 1} PDF не найден код грузоместа")
        if found in seen:
            raise ValueError(f"Код грузоместа {found} повторяется в PDF")
        seen.add(found)
        out.append(CargoUnitPage(page_index=index, cargo_code=found))
    if not out:
        raise ValueError("В PDF нет кодов грузомест")
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for key in ("id", "requestId", "marketplaceRequestId"):
            nested = _int(value.get(key))
            if nested is not None:
                return nested
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _walk(raw: dict[str, Any], *keys: str) -> Any:
    cur: Any = raw
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _first_text(raw: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        value = _walk(raw, *path) if path else None
        text = _text(value)
        if text:
            return text
    return ""


def _warehouse_name(raw: Any) -> str:
    data = _as_dict(raw)
    return _first_text(
        data,
        (
            ("name",),
            ("warehouseName",),
            ("title",),
            ("address", "name"),
            ("address", "fullAddress"),
            ("address", "city"),
        ),
    ) or _text(raw)


def normalize_supply_request(raw: dict[str, Any]) -> dict[str, Any]:
    """Свести заявку FBY Partner API к полям панели и задания."""
    data = _as_dict(raw)
    request_id = _int(data.get("id") or data.get("requestId"))
    marketplace_request_id = _text(
        data.get("marketplaceRequestId") or data.get("marketplace_request_id")
    )
    warehouse_request_id = _text(
        data.get("warehouseRequestId") or data.get("warehouse_request_id")
    )
    subtype = _text(data.get("subtype") or data.get("subType")).upper()
    parent_raw = _as_dict(data.get("parentLink") or data.get("parent"))
    parent_id = _int(
        parent_raw.get("id")
        or _walk(parent_raw, "id", "id")
        or data.get("parentId")
        or data.get("parentRequestId")
    )
    is_child = any(marker in subtype for marker in CHILD_SUBTYPE_MARKERS) or (
        bool(parent_id) and bool(marketplace_request_id)
    )
    planned = _int(
        _walk(data, "itemsCount", "planned")
        or _walk(data, "counts", "planned")
        or data.get("plannedCount")
        or data.get("itemsPlanned")
    )
    status = _text(data.get("status") or data.get("requestStatus"))
    transit_wh = data.get("transitWarehouse") or data.get("firstMileWarehouse")
    target_wh = (
        data.get("targetWarehouse")
        or data.get("destinationWarehouse")
        or data.get("warehouse")
    )
    transit_at = _first_text(
        data,
        (
            ("transitDateTime",),
            ("requestedDateTime",),
            ("firstMileDateTime",),
            ("transitWarehouse", "requestedDateTime"),
            ("transitWarehouse", "dateTime"),
        ),
    )
    accept_at = _first_text(
        data,
        (
            ("plannedDateTime",),
            ("targetDateTime",),
            ("destinationDateTime",),
            ("targetWarehouse", "requestedDateTime"),
            ("targetWarehouse", "dateTime"),
        ),
    )
    vrc_id = parent_id
    display_vrc = f"ВРЦ-{vrc_id}" if vrc_id else ""
    display_supply = marketplace_request_id or _text(request_id)
    return {
        "request_id": request_id,
        "marketplace_request_id": marketplace_request_id,
        "warehouse_request_id": warehouse_request_id,
        "parent_request_id": parent_id,
        "vrc_id": vrc_id,
        "vrc_label": display_vrc,
        "display_id": " / ".join(part for part in (display_supply, display_vrc) if part),
        "type": _text(data.get("type") or "SUPPLY").upper() or "SUPPLY",
        "subtype": subtype,
        "is_child": bool(is_child),
        "status": status,
        "status_hidden": status.upper() in HIDDEN_SUPPLY_STATUSES
        or bool(_CANCELLED_DOC.search(status)),
        "planned_qty": int(planned or 0),
        "warehouse_name": _warehouse_name(target_wh),
        "transit_warehouse": _warehouse_name(transit_wh),
        "transit_at": transit_at,
        "accept_at": accept_at,
        "updated_at": _text(data.get("updatedAt") or data.get("updated_at")),
    }


def normalize_supply_item(raw: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(raw)
    sku = _text(
        data.get("offerId")
        or data.get("shopSku")
        or data.get("article")
        or data.get("sku")
        or data.get("vendorCode")
    )
    name = _text(data.get("name") or data.get("offerName") or data.get("title"))
    qty = _int(
        _walk(data, "counts", "planned")
        or _walk(data, "count", "planned")
        or data.get("plannedCount")
        or data.get("count")
        or data.get("quantity")
    )
    barcodes: list[str] = []
    for key in ("barcode", "ean", "barcodes"):
        value = data.get(key)
        if isinstance(value, list):
            barcodes.extend(_text(item) for item in value if _text(item))
        elif _text(value):
            barcodes.append(_text(value))
    return {
        "sku": sku,
        "name": name,
        "planned_qty": int(qty or 0),
        "barcodes": list(dict.fromkeys(barcodes)),
    }


class YandexFboApi:
    """Клиент FBY. Campaign ID FBS из настроек не используется."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 40,
        session: requests.Session | None = None,
        base_url: str = YANDEX_PARTNER_BASE,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.timeout = timeout
        self._http = session or requests.Session()
        self.base_url = str(base_url or YANDEX_PARTNER_BASE).rstrip("/")
        self._fby_campaign_id: int | None = None

    def is_configured(self) -> bool:
        return is_value_configured(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"Api-Key": self.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _raise_api(self, resp: requests.Response, *, what: str) -> None:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        errors = _as_list(_as_dict(payload).get("errors"))
        messages = [
            _text(item.get("message") if isinstance(item, dict) else item)
            for item in errors
        ]
        detail = "; ".join(part for part in messages if part) or _text(
            _as_dict(payload).get("message")
        )
        raise ValueError(detail or f"{what}: HTTP {resp.status_code}")

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.get(
            self._url(path),
            headers=self._headers(),
            params=params or {},
            timeout=self.timeout,
        )
        if not resp.ok:
            self._raise_api(resp, what=path)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ValueError(f"Некорректный ответ Яндекса: {path}") from exc
        return data if isinstance(data, dict) else {}

    def _post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._http.post(
            self._url(path),
            headers=self._headers(),
            params=params or {},
            json=body or {},
            timeout=self.timeout,
        )
        if not resp.ok:
            self._raise_api(resp, what=path)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ValueError(f"Некорректный ответ Яндекса: {path}") from exc
        return data if isinstance(data, dict) else {}

    def find_fby_campaign_id(self) -> int:
        if self._fby_campaign_id:
            return self._fby_campaign_id
        payload = self._get("/v2/campaigns")
        campaigns = _as_list(payload.get("campaigns") or _walk(payload, "result", "campaigns"))
        for item in campaigns:
            data = _as_dict(item)
            placement = _text(
                data.get("placementType") or data.get("placement_type")
            ).upper()
            if placement != FBY_PLACEMENT:
                continue
            campaign_id = _int(data.get("id") or data.get("campaignId"))
            if campaign_id:
                self._fby_campaign_id = campaign_id
                return campaign_id
        raise ValueError("Не найдена кампания Yandex Market с placementType=FBY")

    def _requests_path(self, suffix: str = "") -> str:
        campaign_id = self.find_fby_campaign_id()
        return f"/v2/campaigns/{campaign_id}/supply-requests{suffix}"

    def _iter_supply_pages(
        self, body: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        token = ""
        rows: list[dict[str, Any]] = []
        for _ in range(50):
            params: dict[str, Any] = {"limit": 100}
            if token:
                params["page_token"] = token
            payload = self._post(self._requests_path(), body or {}, params=params)
            result = _as_dict(payload.get("result") or payload)
            items = _as_list(
                result.get("requests")
                or result.get("supplyRequests")
                or payload.get("requests")
            )
            rows.extend(_as_dict(item) for item in items if isinstance(item, dict))
            paging = _as_dict(result.get("paging") or payload.get("paging"))
            token = _text(paging.get("nextPageToken") or paging.get("next_page_token"))
            if not token:
                break
        return rows

    def get_supply(self, request_id: int) -> dict[str, Any]:
        rid = int(request_id)
        payload = self._post(self._requests_path(), {"requestIds": [rid]})
        result = _as_dict(payload.get("result") or payload)
        items = _as_list(
            result.get("requests")
            or result.get("supplyRequests")
            or payload.get("requests")
        )
        for item in items:
            data = _as_dict(item)
            if _int(data.get("id") or data.get("requestId")) == rid:
                return normalize_supply_request(data)
        raise ValueError(f"Заявка FBY {rid} не найдена")

    def list_child_supplies(self) -> list[dict[str, Any]]:
        rows = []
        for raw in self._iter_supply_pages({"types": ["SUPPLY"]}):
            item = normalize_supply_request(raw)
            if not item.get("is_child") or not item.get("request_id"):
                continue
            if item.get("status_hidden"):
                continue
            rows.append(item)
        rows.sort(key=lambda row: int(row.get("request_id") or 0), reverse=True)
        return rows

    def get_items(self, request_id: int) -> list[dict[str, Any]]:
        payload = self._post(
            self._requests_path("/items"),
            {"requestId": int(request_id)},
        )
        result = _as_dict(payload.get("result") or payload)
        raw_items = _as_list(result.get("items") or payload.get("items"))
        items = [normalize_supply_item(_as_dict(row)) for row in raw_items]
        return [item for item in items if item["sku"] and item["planned_qty"] > 0]

    def list_documents(self, request_id: int) -> list[dict[str, Any]]:
        payload = self._post(
            self._requests_path("/documents"),
            {"requestId": int(request_id)},
        )
        result = _as_dict(payload.get("result") or payload)
        docs = _as_list(result.get("documents") or payload.get("documents"))
        out = []
        for item in docs:
            data = _as_dict(item)
            out.append(
                {
                    "type": _text(data.get("type") or data.get("documentType")).upper(),
                    "url": _text(data.get("url") or data.get("downloadUrl")),
                    "name": _text(data.get("name") or data.get("filename")),
                }
            )
        return out

    def download_cargo_units_pdf(self, request_id: int) -> bytes:
        docs = self.list_documents(request_id)
        cargo = next((item for item in docs if item["type"] == "CARGO_UNITS"), None)
        if cargo is None or not cargo.get("url"):
            raise ValueError("PDF грузомест ещё недоступен")
        resp = self._http.get(str(cargo["url"]), timeout=self.timeout)
        if not resp.ok:
            raise ValueError(f"Не удалось скачать PDF грузомест: HTTP {resp.status_code}")
        content = resp.content or b""
        if not content.startswith(b"%PDF"):
            raise ValueError("Документ CARGO_UNITS не является PDF")
        return content


def get_configured_yandex_fbo_api(settings: Settings | None) -> YandexFboApi | None:
    if settings is None:
        return None
    api_key = str(getattr(settings, "yandex_api_key", "") or "").strip()
    api = YandexFboApi(api_key)
    return api if api.is_configured() else None
