import logging
from datetime import datetime, timedelta, timezone

import requests

from app.adapters.base import MarketplaceAdapter, ReservationAction, is_value_configured

logger = logging.getLogger(__name__)

# Cutoff = время, к которому продавец должен собрать отправление. Новые заказы часто имеют cutoff в будущем,
# поэтому нельзя брать только «cutoff_to = сейчас» — список будет пустым.
# Назад — ~30 суток (меньше объём, как у WB); вперёд — 90 — бывают отгрузки на дальнюю дату.
_CUTOFF_LOOKBACK_DAYS = 30
_CUTOFF_LOOKAHEAD_DAYS = 90

# Резерв только до передачи в доставку (без acceptance_in_progress — приёмка на стороне Ozon).
# Не включаем delivering и дальше — товар уже у логистики, на витрине это не «резерв склада».
_FBS_RESERVE_STATUSES: tuple[str, ...] = (
    "awaiting_registration",
    "awaiting_approve",
    "awaiting_packaging",
    "awaiting_deliver",
)


class OzonAdapter(MarketplaceAdapter):
    name = "ozon"
    base_url = "https://api-seller.ozon.ru"
    # Полный снимок резервируемых отправлений — можно снимать резервы при отмене/смене статуса.
    supports_reserve_reconciliation = True

    def __init__(self, client_id: str, api_key: str, warehouse_id: str) -> None:
        self.client_id = client_id
        self.api_key = api_key
        self.warehouse_id = warehouse_id

    def is_configured(self) -> bool:
        return is_value_configured(self.client_id) and is_value_configured(self.api_key)

    @staticmethod
    def _to_ozon_datetime(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _unfulfilled_payload(self, status: str, offset: int) -> dict:
        now = datetime.now(timezone.utc)
        cutoff_from = self._to_ozon_datetime(now - timedelta(days=_CUTOFF_LOOKBACK_DAYS))
        cutoff_to = self._to_ozon_datetime(now + timedelta(days=_CUTOFF_LOOKAHEAD_DAYS))
        filter_body: dict = {
            "cutoff_from": cutoff_from,
            "cutoff_to": cutoff_to,
            "status": status,
        }
        return {
            "dir": "ASC",
            "filter": filter_body,
            "limit": 1000,
            "offset": offset,
            "with": {
                "analytics_data": False,
                "barcodes": False,
                "financial_data": False,
            },
        }

    def fetch_reservations_full(self) -> list[ReservationAction]:
        """Полный список резервируемых FBS-отправлений (все статусы из _FBS_RESERVE_STATUSES)."""
        if not self.is_configured():
            return []
        headers = {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        seen: set[tuple[str, str]] = set()
        actions: list[ReservationAction] = []

        for status in _FBS_RESERVE_STATUSES:
            offset = 0
            while True:
                payload = self._unfulfilled_payload(status=status, offset=offset)
                try:
                    response = requests.post(
                        f"{self.base_url}/v3/posting/fbs/unfulfilled/list",
                        headers=headers,
                        json=payload,
                        timeout=30,
                    )
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    logger.warning("Ozon unfulfilled/list status=%s offset=%s: %s", status, offset, exc)
                    break
                result = response.json().get("result", {}) or {}
                postings = result.get("postings") or []
                for posting in postings:
                    posting_number = str(posting.get("posting_number", "")).strip()
                    if not posting_number:
                        continue
                    for product in posting.get("products", []) or []:
                        sku = str(product.get("offer_id") or product.get("sku") or "").strip()
                        quantity = int(product.get("quantity", 0))
                        if not sku or quantity <= 0:
                            continue
                        key = (posting_number, sku)
                        if key in seen:
                            continue
                        seen.add(key)
                        external_id = f"{posting_number}:{sku}"
                        actions.append(
                            ReservationAction(
                                source=self.name,
                                external_order_id=external_id,
                                sku=sku,
                                quantity=quantity,
                            )
                        )
                if len(postings) < 1000:
                    break
                offset += 1000

        return actions

    def fetch_reservations_delta(self, date_from: int, date_to: int) -> list[ReservationAction]:
        """
        Дельта по времени: у Ozon в unfulfilled/list нет надёжного «дешёвого» окна без потери активных отправлений,
        поэтому запрос тот же, что и полный; отличие только в координаторе (reconcile реже).
        """
        _ = date_from
        _ = date_to
        return self.fetch_reservations_full()

    def fetch_new_reservations(self) -> list[ReservationAction]:
        return self.fetch_reservations_full()

    def fetch_ready_to_ship_external_ids(self) -> set[str]:
        """
        Возвращает набор external_order_id для позиций отправлений Ozon со статусом awaiting_deliver
        (готово к отгрузке), в формате "{posting_number}:{sku}".
        """
        if not self.is_configured():
            return set()
        headers = {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        external_ids: set[str] = set()
        offset = 0
        status = "awaiting_deliver"
        while True:
            payload = self._unfulfilled_payload(status=status, offset=offset)
            response = requests.post(
                f"{self.base_url}/v3/posting/fbs/unfulfilled/list",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json().get("result", {}) or {}
            postings = result.get("postings") or []
            for posting in postings:
                posting_number = str(posting.get("posting_number", "")).strip()
                if not posting_number:
                    continue
                for product in posting.get("products", []) or []:
                    sku = str(product.get("offer_id") or product.get("sku") or "").strip()
                    quantity = int(product.get("quantity", 0))
                    if not sku or quantity <= 0:
                        continue
                    external_ids.add(f"{posting_number}:{sku}")
            if len(postings) < 1000:
                break
            offset += 1000
        return external_ids

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        if not self.is_configured() or not available_stock_by_sku:
            return
        if not is_value_configured(self.warehouse_id):
            raise RuntimeError("Set OZON_WAREHOUSE_ID in .env for Ozon stock sync")
        headers = {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        stocks = [
            {
                "offer_id": sku,
                "stock": max(available, 0),
                "warehouse_id": int(self.warehouse_id),
            }
            for sku, available in available_stock_by_sku.items()
        ]
        # Ozon: от 1 до 100 позиций в одном запросе.
        chunk_size = 100
        for start in range(0, len(stocks), chunk_size):
            chunk = stocks[start : start + chunk_size]
            response = requests.post(
                f"{self.base_url}/v2/products/stocks",
                headers=headers,
                json={"stocks": chunk},
                timeout=30,
            )
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                details = response.text.strip()
                if details:
                    raise requests.HTTPError(f"{exc}; body={details}") from exc
                raise
