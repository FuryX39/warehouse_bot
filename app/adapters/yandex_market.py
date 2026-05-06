from datetime import datetime, timedelta, timezone

import requests

from app.adapters.base import MarketplaceAdapter, ReservationAction, is_value_configured

# Лимит Yandex: не больше 30 суток между fromDate и toDate (формат YYYY-MM-DD).
_ORDER_LIST_SPAN_DAYS = 30
# Сдвиг правой границы окна вперёд, чтобы захватывать заказы с дальней датой отгрузки.
_ORDER_LIST_LOOKAHEAD_DAYS = 10
# Для этого эндпоинта кампаний используем поддерживаемый статус PROCESSING.
_RESERVE_ORDER_STATUSES: tuple[str, ...] = ("PROCESSING",)
# Не резервируем отмены/просрочки/отказы и прочие причины, где товар уже не должен держаться в резерве.
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


class YandexMarketAdapter(MarketplaceAdapter):
    name = "yandex_market"
    base_url = "https://api.partner.market.yandex.ru"
    supports_reserve_reconciliation = True
    # У Яндекса дельта сейчас реализована тем же полным снимком, поэтому reconcile нужен в каждом цикле.
    reconcile_on_delta = True
    ready_to_ship_substatus = "READY_TO_SHIP"

    def __init__(self, campaign_id: str, api_key: str) -> None:
        self.campaign_id = campaign_id
        self.api_key = api_key

    def is_configured(self) -> bool:
        return is_value_configured(self.campaign_id) and is_value_configured(self.api_key)

    @staticmethod
    def _orders_date_range_query() -> dict[str, str]:
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=_ORDER_LIST_LOOKAHEAD_DAYS)
        start = end - timedelta(days=_ORDER_LIST_SPAN_DAYS)
        fmt = "%Y-%m-%d"
        return {"fromDate": start.strftime(fmt), "toDate": end.strftime(fmt)}

    def fetch_reservations_full(self) -> list[ReservationAction]:
        if not self.is_configured():
            return []
        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        actions_by_external_id: dict[str, ReservationAction] = {}
        limit = 50
        for status in _RESERVE_ORDER_STATUSES:
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
                orders = body.get("orders", []) or []
                for action in self._orders_to_actions(orders):
                    actions_by_external_id[action.external_order_id] = action
                paging = body.get("paging") or {}
                next_token = str(paging.get("nextPageToken") or "").strip()
                if not next_token:
                    break
                page_token = next_token
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
        Yandex Market: status=PROCESSING + substatus=READY_TO_SHIP.
        Возвращает external ids в формате "{orderId}:{sku}".
        """
        if not self.is_configured():
            return set()
        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        external_ids: set[str] = set()
        page_token: str | None = None
        limit = 50
        while True:
            params: dict[str, str | int] = {
                "status": "PROCESSING",
                "substatus": self.ready_to_ship_substatus,
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
            orders = body.get("orders", []) or []
            for order in orders:
                order_id = str(order.get("id", "")).strip()
                if not order_id:
                    continue
                for item in order.get("items", []) or []:
                    sku = str(item.get("offerId") or item.get("shopSku") or "").strip()
                    qty = int(item.get("count", 0))
                    if sku and qty > 0:
                        external_ids.add(f"{order_id}:{sku}")
            paging = body.get("paging") or {}
            next_token = str(paging.get("nextPageToken") or "").strip()
            if not next_token:
                break
            page_token = next_token
        return external_ids

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        if not self.is_configured() or not available_stock_by_sku:
            return
        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
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
