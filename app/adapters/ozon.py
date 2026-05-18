import logging
from dataclasses import dataclass
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

# В ЛК Ozon: «Ожидает отгрузки»
OZON_AWAITING_SHIPMENT_STATUSES: tuple[str, ...] = ("awaiting_deliver",)

_PACKAGE_LABEL_MAX_POSTINGS = 20


@dataclass(frozen=True)
class OzonFbsPosting:
    posting_number: str
    status: str
    lines: tuple[tuple[str, int], ...]  # (offer_id/sku, quantity)


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

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

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
        Возвращает набор external_order_id для позиций отправлений Ozon,
        которые можно отгружать (не «новые»):
        awaiting_approve / awaiting_packaging / awaiting_deliver.
        Формат id: "{posting_number}:{sku}".
        """
        if not self.is_configured():
            return set()
        headers = {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        external_ids: set[str] = set()
        ship_statuses: tuple[str, ...] = ("awaiting_approve", "awaiting_packaging", "awaiting_deliver")
        for status in ship_statuses:
            offset = 0
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

    def list_awaiting_shipment_postings(
        self,
        statuses: tuple[str, ...] = OZON_AWAITING_SHIPMENT_STATUSES,
    ) -> list[OzonFbsPosting]:
        """
        FBS-отправления в статусе «ожидает отгрузки» (по умолчанию awaiting_deliver).
        Одна запись на posting_number (в отправлении может быть несколько SKU).
        """
        if not self.is_configured():
            return []
        headers = self._headers()
        by_posting: dict[str, OzonFbsPosting] = {}

        for status in statuses:
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
                    logger.warning(
                        "Ozon awaiting shipment list status=%s offset=%s: %s", status, offset, exc
                    )
                    break
                result = response.json().get("result", {}) or {}
                postings = result.get("postings") or []
                for posting in postings:
                    posting_number = str(posting.get("posting_number", "")).strip()
                    if not posting_number:
                        continue
                    line_map: dict[str, int] = {}
                    if posting_number in by_posting:
                        for sku, qty in by_posting[posting_number].lines:
                            line_map[sku] = line_map.get(sku, 0) + qty
                    for product in posting.get("products", []) or []:
                        sku = str(product.get("offer_id") or product.get("sku") or "").strip()
                        quantity = int(product.get("quantity", 0))
                        if not sku or quantity <= 0:
                            continue
                        line_map[sku] = line_map.get(sku, 0) + quantity
                    if not line_map:
                        continue
                    lines = tuple(sorted(line_map.items(), key=lambda x: x[0]))
                    by_posting[posting_number] = OzonFbsPosting(
                        posting_number=posting_number,
                        status=status,
                        lines=lines,
                    )
                if len(postings) < 1000:
                    break
                offset += 1000

        return sorted(by_posting.values(), key=lambda p: p.posting_number)

    def fetch_package_label_pdf_parts(
        self,
        posting_numbers: list[str],
        *,
        chunk_size: int = _PACKAGE_LABEL_MAX_POSTINGS,
    ) -> tuple[list[tuple[str, bytes]], list[str]]:
        """
        Этикетки FBS (PDF) через POST /v2/posting/fbs/package-label.
        До chunk_size posting_number за запрос. Возвращает [(имя_файла, pdf_bytes), ...].
        """
        if not self.is_configured():
            return [], ["Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)"]
        unique = list(dict.fromkeys(str(p).strip() for p in posting_numbers if str(p).strip()))
        if not unique:
            return [], []

        headers = self._headers()
        parts: list[tuple[str, bytes]] = []
        warnings: list[str] = []
        size = max(1, min(int(chunk_size), _PACKAGE_LABEL_MAX_POSTINGS))

        for start in range(0, len(unique), size):
            chunk = unique[start : start + size]
            try:
                response = requests.post(
                    f"{self.base_url}/v2/posting/fbs/package-label",
                    headers=headers,
                    json={"posting_number": chunk},
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
                warnings.append(f"Этикетки {chunk[0]}… ({len(chunk)} шт.): {detail}")
                continue

            content = response.content
            if not content:
                warnings.append(f"Пустой PDF для: {', '.join(chunk)}")
                continue
            if not content.startswith(b"%PDF"):
                warnings.append(
                    f"Ответ не похож на PDF для: {', '.join(chunk)} "
                    f"(Content-Type: {response.headers.get('Content-Type', '')})"
                )
                continue
            if len(chunk) == 1:
                fname = f"ozon_label_{chunk[0]}.pdf"
            else:
                fname = f"ozon_labels_{chunk[0]}_{len(chunk)}.pdf"
            parts.append((fname, content))

        return parts, warnings

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
