import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from app.adapters.base import MarketplaceAdapter, ReservationAction, is_value_configured

logger = logging.getLogger(__name__)

# Лимит Yandex: не больше 30 суток между fromDate и toDate (формат YYYY-MM-DD).
_ORDER_LIST_SPAN_DAYS = 30
# Сдвиг правой границы окна вперёд, чтобы захватывать заказы с дальней датой отгрузки.
_ORDER_LIST_LOOKAHEAD_DAYS = 10
# Для этого эндпоинта кампаний используем поддерживаемый статус PROCESSING.
_RESERVE_ORDER_STATUSES: tuple[str, ...] = ("PROCESSING",)
# Не резервируем отмены/просрочки/отказы и прочие причины, где товар уже не должен держаться в резерве.
# Заказы «ожидают сборки» (FBS) — аналог Ozon awaiting_deliver для /yandex_labels.
YANDEX_AWAITING_ASSEMBLY_STATUS = "PROCESSING"
YANDEX_AWAITING_ASSEMBLY_SUBSTATUS = "STARTED"
YANDEX_LABEL_FORMATS: tuple[str, ...] = ("A9_HORIZONTALLY", "A9", "A7", "A4")

_NO_RESERVE_SUBSTATUS_PREFIXES: tuple[str, ...] = (
    "PENDING_CANCELLED",
    "SHOP_PENDING_CANCELLED",
    "PROCESSING_EXPIRED",
    "PENDING_EXPIRED",
    "RESERVATION_EXPIRED",
    "RESERVATION_FAILED",
    "USER_",
    "SHOP_FAILED",
    "DELIVERY_",
    "WAREHOUSE_FAILED",
)


@dataclass(frozen=True)
class YandexFbsOrder:
    order_id: str
    status: str
    substatus: str
    lines: tuple[tuple[str, int], ...]


class YandexMarketAdapter(MarketplaceAdapter):
    name = "yandex_market"
    base_url = "https://api.partner.market.yandex.ru"
    supports_reserve_reconciliation = True
    # У Яндекса дельта сейчас реализована тем же полным снимком, поэтому reconcile нужен в каждом цикле.
    reconcile_on_delta = True
    awaiting_assembly_substatus = "STARTED"

    def __init__(self, campaign_id: str, api_key: str) -> None:
        self.campaign_id = campaign_id
        self.api_key = api_key

    def is_configured(self) -> bool:
        return is_value_configured(self.campaign_id) and is_value_configured(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _orders_date_range_query() -> dict[str, str]:
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=_ORDER_LIST_LOOKAHEAD_DAYS)
        start = end - timedelta(days=_ORDER_LIST_SPAN_DAYS)
        fmt = "%Y-%m-%d"
        return {"fromDate": start.strftime(fmt), "toDate": end.strftime(fmt)}

    def _iter_orders(
        self,
        *,
        status: str = YANDEX_AWAITING_ASSEMBLY_STATUS,
    ):
        """Постраничный обход заказов кампании."""
        if not self.is_configured():
            return
        headers = self._headers()
        limit = 50
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "status": status,
                "limit": limit,
                **self._orders_date_range_query(),
            }
            if page_token:
                params["page_token"] = page_token
            response = requests.get(
                f"{self.base_url}/campaigns/{self.campaign_id}/orders",
                headers=headers,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            body = response.json() or {}
            for order in body.get("orders", []) or []:
                yield order
            paging = body.get("paging") or {}
            next_token = str(paging.get("nextPageToken") or "").strip()
            if not next_token:
                break
            page_token = next_token

    def fetch_reservations_full(self) -> list[ReservationAction]:
        if not self.is_configured():
            return []
        actions_by_external_id: dict[str, ReservationAction] = {}
        for status in _RESERVE_ORDER_STATUSES:
            for order in self._iter_orders(status=status):
                for action in self._orders_to_actions([order]):
                    actions_by_external_id[action.external_order_id] = action
        return list(actions_by_external_id.values())

    def fetch_reservations_delta(self, date_from: int, date_to: int) -> list[ReservationAction]:
        """
        API отдаёт заказы в PROCESSING целиком; дельта = тот же запрос (как Ozon), reconcile реже — в координаторе.
        """
        _ = date_from
        _ = date_to
        return self.fetch_reservations_full()

    def fetch_new_reservations(self) -> list[ReservationAction]:
        return self.fetch_reservations_full()

    @staticmethod
    def _orders_to_actions(orders: list) -> list[ReservationAction]:
        src = "yandex_market"
        actions: list[ReservationAction] = []
        for order in orders:
            substatus = str(order.get("substatus") or "").strip().upper()
            if substatus and any(substatus.startswith(prefix) for prefix in _NO_RESERVE_SUBSTATUS_PREFIXES):
                continue
            order_id = str(order.get("id", ""))
            for item in order.get("items", []):
                sku = str(item.get("offerId") or item.get("shopSku") or "").strip()
                quantity = int(item.get("count", 0))
                if order_id and sku and quantity > 0:
                    actions.append(
                        ReservationAction(
                            source=src,
                            external_order_id=f"{order_id}:{sku}",
                            sku=sku,
                            quantity=quantity,
                        )
                    )
        return actions

    def fetch_ready_to_ship_external_ids(self) -> set[str]:
        """
        Yandex Market: вернуть external ids заказов в ожидании сборки (substatus=STARTED).
        Берём все PROCESSING и фильтруем STARTED локально, чтобы не зависеть от
        серверной интерпретации query-параметра substatus.
        Формат id: "{orderId}:{sku}".
        """
        if not self.is_configured():
            return set()
        external_ids: set[str] = set()
        for order in self._iter_orders(status=YANDEX_AWAITING_ASSEMBLY_STATUS):
            substatus = str(order.get("substatus") or "").strip().upper()
            if substatus != self.awaiting_assembly_substatus:
                continue
            order_id = str(order.get("id", "")).strip()
            if not order_id:
                continue
            for item in order.get("items", []) or []:
                sku = str(item.get("offerId") or item.get("shopSku") or "").strip()
                qty = int(item.get("count", 0))
                if sku and qty > 0:
                    external_ids.add(f"{order_id}:{sku}")
        return external_ids

    def list_awaiting_assembly_orders(
        self,
        *,
        substatus: str = YANDEX_AWAITING_ASSEMBLY_SUBSTATUS,
    ) -> list[YandexFbsOrder]:
        """
        FBS-заказы в ожидании сборки (PROCESSING + substatus STARTED по умолчанию).
        Одна запись на order_id (в заказе может быть несколько SKU).
        """
        if not self.is_configured():
            return []
        want_sub = str(substatus or YANDEX_AWAITING_ASSEMBLY_SUBSTATUS).strip().upper()
        by_order: dict[str, YandexFbsOrder] = {}

        for order in self._iter_orders(status=YANDEX_AWAITING_ASSEMBLY_STATUS):
            order_sub = str(order.get("substatus") or "").strip().upper()
            if order_sub != want_sub:
                continue
            order_id = str(order.get("id", "")).strip()
            if not order_id:
                continue
            line_map: dict[str, int] = {}
            if order_id in by_order:
                for sku, qty in by_order[order_id].lines:
                    line_map[sku] = line_map.get(sku, 0) + qty
            for item in order.get("items", []) or []:
                sku = str(item.get("offerId") or item.get("shopSku") or "").strip()
                qty = int(item.get("count", 0))
                if not sku or qty <= 0:
                    continue
                line_map[sku] = line_map.get(sku, 0) + qty
            if not line_map:
                continue
            lines = tuple(sorted(line_map.items(), key=lambda x: x[0]))
            by_order[order_id] = YandexFbsOrder(
                order_id=order_id,
                status=YANDEX_AWAITING_ASSEMBLY_STATUS,
                substatus=order_sub,
                lines=lines,
            )

        return sorted(by_order.values(), key=lambda o: o.order_id)

    def fetch_order_label_pdf_parts(
        self,
        order_ids: list[str],
        *,
        label_format: str = "A9_HORIZONTALLY",
    ) -> tuple[list[tuple[str, bytes]], list[str]]:
        """
        PDF-этикетки FBS: GET /v2/campaigns/{campaignId}/orders/{orderId}/delivery/labels.
        Порядок order_ids сохраняется.
        """
        if not self.is_configured():
            return [], ["Yandex Market API не настроен (YANDEX_CAMPAIGN_ID / YANDEX_API_KEY)"]
        unique = list(dict.fromkeys(str(o).strip() for o in order_ids if str(o).strip()))
        if not unique:
            return [], []

        fmt = (label_format or "A9_HORIZONTALLY").strip()
        if fmt not in YANDEX_LABEL_FORMATS:
            fmt = "A9_HORIZONTALLY"

        headers = self._headers()
        parts: list[tuple[str, bytes]] = []
        warnings: list[str] = []

        for order_id in unique:
            try:
                response = requests.get(
                    f"{self.base_url}/v2/campaigns/{self.campaign_id}/orders/{order_id}/delivery/labels",
                    headers=headers,
                    params={"format": fmt},
                    timeout=90,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                detail = str(exc)
                resp = getattr(exc, "response", None)
                if resp is not None:
                    body = (resp.text or "").strip()[:500]
                    if body:
                        detail = f"{detail}; {body}"
                warnings.append(f"Заказ {order_id}: {detail}")
                continue

            content = response.content
            if not content:
                warnings.append(f"Пустой PDF для заказа {order_id}")
                continue
            if not content.startswith(b"%PDF"):
                warnings.append(
                    f"Ответ не похож на PDF для заказа {order_id} "
                    f"(Content-Type: {response.headers.get('Content-Type', '')})"
                )
                continue
            parts.append((f"yandex_label_{order_id}.pdf", content))

        return parts, warnings

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        if not self.is_configured() or not available_stock_by_sku:
            return
        headers = self._headers()
        payload = {
            "skus": [
                {
                    "sku": sku,
                    "items": [{"count": max(available, 0)}],
                }
                for sku, available in available_stock_by_sku.items()
            ]
        }
        response = requests.put(
            f"{self.base_url}/campaigns/{self.campaign_id}/offers/stocks",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
