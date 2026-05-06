import logging
import time

import requests
from requests import exceptions as req_exc

from app.adapters.base import MarketplaceAdapter, ReservationAction, is_value_configured

logger = logging.getLogger(__name__)

_STOCKS_CHUNK = 1000
# Повторы при обрыве TLS/сети (SSLEOFError, reset и т.п.) на VPS и «длинных» маршрутах до WB.
_MP_HTTP_RETRIES = 5
_MP_RETRY_BASE_DELAY = 2.0
_ORDERS_PAGE_LIMIT = 1000
_STATUS_BATCH = 1000
# GET /api/v3/orders — не больше 30 дней за запрос; полный синк: одно окно (30 суток назад — сейчас).
_ORDER_HISTORY_WINDOWS = 1
_ORDER_WINDOW_DAYS = 30
_MAX_ORDER_PAGES_PER_WINDOW = 500

_CONTENT_BASE = "https://content-api.wildberries.ru"
_VENDOR_CHRT_CACHE_TTL_SEC = 600.0

# Резерв только пока сборка ещё «на нас»: new / confirm. complete = передано в доставку (уже не наш склад).
_RESERVE_SUPPLIER_STATUSES = frozenset({"new", "confirm"})
# Не резервируем: отмены, продажа, и этапы, где заказ уже у WB/логистики (не на нашем складе).
_WB_NO_RESERVE_STATUSES = frozenset(
    {
        "canceled",
        "canceled_by_client",
        "declined_by_client",
        "defect",
        "canceled_by_carrier",
        "sold",
        "sorted",
        "ready_for_pickup",
        "accepted_by_carrier",
        "sent_to_carrier",
    }
)


def _wb_http_error(response: requests.Response) -> requests.HTTPError:
    body = (response.text or "").strip()
    if len(body) > 800:
        body = body[:800] + "…"
    detail = f"{response.status_code} Client Error: {response.reason} for url: {response.url}"
    if body:
        detail += f" — {body}"
    if response.status_code == 404:
        detail += (
            " Wildberries stock API expects seller warehouse `id` from GET /api/v3/warehouses "
            "(not JWT `oid` / officeId)."
        )
    return requests.HTTPError(detail, response=response)


def _wb_request(method: str, url: str, **kwargs) -> requests.Response:
    """GET/POST/PUT с повторами при временных сбоях TLS и транспорта."""
    transient = (
        req_exc.SSLError,
        req_exc.ConnectionError,
        req_exc.ChunkedEncodingError,
        req_exc.Timeout,
    )
    method_u = method.upper()
    for attempt in range(1, _MP_HTTP_RETRIES + 1):
        try:
            if method_u == "GET":
                return requests.get(url, **kwargs)
            if method_u == "POST":
                return requests.post(url, **kwargs)
            if method_u == "PUT":
                return requests.put(url, **kwargs)
            raise ValueError(f"unsupported HTTP method: {method}")
        except transient as exc:
            logger.warning(
                "Wildberries %s %s — временная ошибка: %s (попытка %s/%s)",
                method_u,
                url,
                exc,
                attempt,
                _MP_HTTP_RETRIES,
            )
            if attempt >= _MP_HTTP_RETRIES:
                raise
            delay = min(_MP_RETRY_BASE_DELAY ** (attempt - 1), 30.0)
            time.sleep(delay)
    raise RuntimeError("unreachable")


class WildberriesAdapter(MarketplaceAdapter):
    name = "wildberries"
    base_url = "https://marketplace-api.wildberries.ru"
    # Полный снимок сборочных заданий (GET /api/v3/orders + статусы) + reconcile в БД.
    supports_reserve_reconciliation = True

    def __init__(self, api_token: str, warehouse_id: str, content_token: str = "") -> None:
        self.api_token = api_token
        self.warehouse_id = warehouse_id
        # Категория «Контент» для списка карточек (vendorCode → chrtId). Часто совпадает с Marketplace-токеном.
        self._content_token = (content_token.strip() or api_token).strip()
        self._vendor_chrt_cache: tuple[float, dict[str, list[int]]] | None = None

    def is_configured(self) -> bool:
        return is_value_configured(self.api_token)

    @staticmethod
    def _should_reserve(supplier_status: str | None, wb_status: str | None) -> bool:
        if supplier_status not in _RESERVE_SUPPLIER_STATUSES:
            return False
        if wb_status and wb_status in _WB_NO_RESERVE_STATUSES:
            return False
        return True

    def _fetch_assembly_orders_window(self, headers: dict[str, str], date_from: int, date_to: int) -> list[dict]:
        """GET /api/v3/orders с пагинацией (limit/next) за один 30-дневный интервал."""
        collected: list[dict] = []
        next_cursor = 0
        prev_next: int | None = None
        for _ in range(_MAX_ORDER_PAGES_PER_WINDOW):
            response = _wb_request(
                "GET",
                f"{self.base_url}/api/v3/orders",
                headers=headers,
                params={
                    "limit": _ORDERS_PAGE_LIMIT,
                    "next": next_cursor,
                    "dateFrom": date_from,
                    "dateTo": date_to,
                },
                timeout=90,
            )
            if not response.ok:
                raise _wb_http_error(response)
            data = response.json() or {}
            batch = data.get("orders") or []
            if not batch:
                break
            collected.extend(o for o in batch if isinstance(o, dict))
            try:
                next_cursor = int(data.get("next", 0))
            except (TypeError, ValueError):
                break
            if prev_next is not None and next_cursor == prev_next:
                break
            prev_next = next_cursor
        return collected

    def _fetch_all_assembly_orders(self, headers: dict[str, str]) -> list[dict]:
        now = int(time.time())
        day = 86400
        seen: set[int] = set()
        merged: list[dict] = []
        for w in range(_ORDER_HISTORY_WINDOWS):
            date_to = now - w * _ORDER_WINDOW_DAYS * day
            date_from = date_to - _ORDER_WINDOW_DAYS * day
            batch = self._fetch_assembly_orders_window(headers, date_from, date_to)
            for order in batch:
                oid = order.get("id")
                try:
                    oid_i = int(oid)
                except (TypeError, ValueError):
                    continue
                if oid_i in seen:
                    continue
                seen.add(oid_i)
                merged.append(order)
        return merged

    def _fetch_assembly_orders_between(self, headers: dict[str, str], date_from: int, date_to: int) -> list[dict]:
        """
        Заказы за [date_from, date_to] (unix), с разбиением на окна ≤30 суток (лимит WB на один запрос).
        Граница date_to — момент начала текущего синка, чтобы заказы во время проведения синка попали в следующий цикл.
        """
        if date_to <= date_from:
            return []
        seen: set[int] = set()
        merged: list[dict] = []
        window = _ORDER_WINDOW_DAYS * 86400
        start = date_from
        while start < date_to:
            end = min(start + window, date_to)
            batch = self._fetch_assembly_orders_window(headers, start, end)
            for order in batch:
                try:
                    oid_i = int(order.get("id"))
                except (TypeError, ValueError):
                    continue
                if oid_i in seen:
                    continue
                seen.add(oid_i)
                merged.append(order)
            start = end
        return merged

    def _build_actions_from_orders(
        self, headers: dict[str, str], orders: list[dict]
    ) -> list[ReservationAction]:
        order_ids: list[int] = []
        for order in orders:
            try:
                order_ids.append(int(order.get("id")))
            except (TypeError, ValueError):
                continue
        status_by_id = self._fetch_statuses(headers, order_ids)

        actions: list[ReservationAction] = []
        skipped_no_status = 0
        skipped_status = 0
        for order in orders:
            try:
                oid_i = int(order.get("id"))
            except (TypeError, ValueError):
                continue
            st = status_by_id.get(oid_i)
            if st is None:
                skipped_no_status += 1
                continue
            supplier_s, wb_s = st
            if not self._should_reserve(supplier_s, wb_s):
                skipped_status += 1
                continue

            order_id = str(oid_i)
            sku = str(
                order.get("supplierArticle")
                or order.get("article")
                or order.get("vendorCode")
                or ""
            ).strip()
            quantity = int(order.get("quantity", 1))
            if order_id and sku and quantity > 0:
                actions.append(
                    ReservationAction(
                        source=self.name,
                        external_order_id=f"{order_id}:{sku}",
                        sku=sku,
                        quantity=quantity,
                    )
                )

        if skipped_no_status:
            logger.warning(
                "Wildberries: %s сборок без строки в /orders/status (пропущены в резервах)",
                skipped_no_status,
            )
        logger.info(
            "Wildberries: сборок=%s, в резерв после фильтра статусов=%s (отсев по статусу=%s)",
            len(orders),
            len(actions),
            skipped_status,
        )
        return actions

    def fetch_reservations_full(self) -> list[ReservationAction]:
        if not self.is_configured():
            return []
        headers = {"Authorization": self.api_token}
        orders = self._fetch_all_assembly_orders(headers)
        return self._build_actions_from_orders(headers, orders)

    def fetch_reservations_delta(self, date_from: int, date_to: int) -> list[ReservationAction]:
        if not self.is_configured():
            return []
        headers = {"Authorization": self.api_token}
        orders = self._fetch_assembly_orders_between(headers, date_from, date_to)
        return self._build_actions_from_orders(headers, orders)

    def _fetch_statuses(
        self, headers: dict[str, str], order_ids: list[int]
    ) -> dict[int, tuple[str | None, str | None]]:
        """POST /api/v3/orders/status — до 1000 id за запрос."""
        out: dict[int, tuple[str | None, str | None]] = {}
        hdrs = {**headers, "Content-Type": "application/json"}
        for i in range(0, len(order_ids), _STATUS_BATCH):
            chunk = order_ids[i : i + _STATUS_BATCH]
            response = _wb_request(
                "POST",
                f"{self.base_url}/api/v3/orders/status",
                headers=hdrs,
                json={"orders": chunk},
                timeout=90,
            )
            if not response.ok:
                raise _wb_http_error(response)
            for row in (response.json() or {}).get("orders") or []:
                if not isinstance(row, dict):
                    continue
                try:
                    oid_i = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                sup = row.get("supplierStatus")
                wb = row.get("wbStatus")
                out[oid_i] = (
                    str(sup).strip() if sup is not None and str(sup).strip() else None,
                    str(wb).strip() if wb is not None and str(wb).strip() else None,
                )
        return out

    def fetch_new_reservations(self) -> list[ReservationAction]:
        """Полный снимок (совместимость с протоколом); координатор вызывает full/delta явно."""
        return self.fetch_reservations_full()

    def fetch_ready_to_ship_external_ids(self, active_external_ids: set[str]) -> set[str]:
        """
        Wildberries FBS: считаем "готово к отгрузке" = заказ добавлен в поставку.

        Алгоритм:
        - берём список поставок (GET /api/v3/supplies),
        - для каждой поставки берём orderIds (GET .../supplies/{supplyId}/order-ids),
        - возвращаем subset активных external ids (формат "{orderId}:{sku}") по совпадению orderId.
        """
        if not self.is_configured() or not active_external_ids:
            return set()

        by_order_id: dict[int, set[str]] = {}
        for ext in active_external_ids:
            parts = ext.split(":", 1)
            if not parts or not parts[0].strip().isdigit():
                continue
            oid = int(parts[0].strip())
            by_order_id.setdefault(oid, set()).add(ext)

        if not by_order_id:
            return set()

        headers = {"Authorization": self.api_token}
        ready: set[str] = set()

        supplies_resp = _wb_request(
            "GET", f"{self.base_url}/api/v3/supplies", headers=headers, timeout=60
        )
        supplies_resp.raise_for_status()
        body = supplies_resp.json() or {}
        supplies = body.get("supplies", body if isinstance(body, list) else []) or []

        supplies = supplies[:50]
        for s in supplies:
            if not isinstance(s, dict):
                continue
            supply_id = str(s.get("id") or s.get("supplyId") or "").strip()
            if not supply_id:
                continue
            order_ids_resp = _wb_request(
                "GET",
                f"{self.base_url}/api/marketplace/v3/supplies/{supply_id}/order-ids",
                headers=headers,
                timeout=60,
            )
            order_ids_resp.raise_for_status()
            order_ids_body = order_ids_resp.json() or {}
            order_ids = order_ids_body.get("orders", order_ids_body.get("orderIds", order_ids_body)) or []
            for oid in order_ids:
                try:
                    oid_int = int(oid)
                except (TypeError, ValueError):
                    continue
                if oid_int in by_order_id:
                    ready.update(by_order_id[oid_int])

        return ready

    @staticmethod
    def _lookup_chrts(vendor_map: dict[str, list[int]], sku: str) -> list[int]:
        s = sku.strip()
        if s in vendor_map:
            return vendor_map[s]
        low = s.lower()
        for k, v in vendor_map.items():
            if k.lower() == low:
                return v
        return []

    def _fetch_vendor_chrt_map(self) -> dict[str, list[int]]:
        """Собрать vendorCode -> [chrtId, ...] по каталогу карточек (Content API)."""
        if not self._content_token:
            return {}

        cursor_req: dict = {"limit": 100}
        merged: dict[str, list[int]] = {}

        while True:
            payload = {
                "settings": {
                    "sort": {"ascending": True},
                    "cursor": cursor_req,
                    "filter": {"withPhoto": -1},
                }
            }
            response = _wb_request(
                "POST",
                f"{_CONTENT_BASE}/content/v2/get/cards/list",
                headers={
                    "Authorization": self._content_token,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if not response.ok:
                raise _wb_http_error(response)

            data = response.json() or {}
            for card in data.get("cards") or []:
                if not isinstance(card, dict):
                    continue
                vc = str(card.get("vendorCode") or "").strip()
                if not vc:
                    continue
                ids: list[int] = []
                for sz in card.get("sizes") or []:
                    if not isinstance(sz, dict):
                        continue
                    raw = sz.get("chrtID")
                    if raw is None:
                        raw = sz.get("chrtId")
                    if raw is None:
                        continue
                    try:
                        ids.append(int(raw))
                    except (TypeError, ValueError):
                        continue
                if ids:
                    merged.setdefault(vc, []).extend(ids)

            for vc, lst in list(merged.items()):
                seen: set[int] = set()
                uniq: list[int] = []
                for x in lst:
                    if x not in seen:
                        seen.add(x)
                        uniq.append(x)
                merged[vc] = uniq

            cur = data.get("cursor") or {}
            total = int(cur.get("total", 0))
            limit = int(cursor_req.get("limit", 100))
            if total < limit:
                break

            updated_at = cur.get("updatedAt")
            nm_id = cur.get("nmID")
            if updated_at is None or nm_id is None:
                logger.warning("Wildberries Content API: нет полей cursor для следующей страницы, обрыв каталога")
                break
            cursor_req = {"limit": limit, "updatedAt": updated_at, "nmID": nm_id}

        return merged

    def _get_vendor_chrt_map_cached(self) -> dict[str, list[int]]:
        now = time.monotonic()
        if self._vendor_chrt_cache is not None:
            ts, m = self._vendor_chrt_cache
            if now - ts < _VENDOR_CHRT_CACHE_TTL_SEC:
                return m
        if not self._content_token:
            return {}
        m = self._fetch_vendor_chrt_map()
        self._vendor_chrt_cache = (now, m)
        return m

    def _sku_to_chrt_amounts(
        self, sku: str, available: int, vendor_map: dict[str, list[int]]
    ) -> list[tuple[int, int]]:
        """Один sku в БД -> одна или несколько позиций chrtId с количеством."""
        if sku.isdigit():
            return [(int(sku), available)]
        chrt_ids = self._lookup_chrts(vendor_map, sku)
        if not chrt_ids:
            return []
        if len(chrt_ids) == 1:
            return [(chrt_ids[0], available)]
        n = len(chrt_ids)
        base, rem = divmod(available, n)
        return [(chrt_ids[i], base + (1 if i < rem else 0)) for i in range(n)]

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        if not self.is_configured() or not self.warehouse_id or not available_stock_by_sku:
            return
        headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
        }

        vendor_map: dict[str, list[int]] = {}
        try:
            vendor_map = self._get_vendor_chrt_map_cached()
        except requests.HTTPError as exc:
            logger.warning("Wildberries: не удалось загрузить каталог карточек (Content): %s", exc)
        except requests.RequestException as exc:
            logger.warning("Wildberries: ошибка сети при загрузке каталога карточек: %s", exc)

        stocks_pairs: list[tuple[int, int]] = []
        unmapped: list[str] = []

        for sku, available in available_stock_by_sku.items():
            key = str(sku).strip()
            if not key:
                continue
            pairs = self._sku_to_chrt_amounts(key, max(available, 0), vendor_map)
            if not pairs:
                unmapped.append(key)
                continue
            stocks_pairs.extend(pairs)

        if unmapped and vendor_map:
            logger.info(
                "Wildberries: для части sku нет vendorCode в каталоге (первые 15): %s",
                unmapped[:15],
            )

        if not stocks_pairs:
            if unmapped:
                logger.warning(
                    "Wildberries: синк остатков пропущен — ни один артикул не сопоставлен с chrtId. "
                    "Проверьте, что product_stocks.sku = vendorCode в WB; для доступа к каталогу нужен токен "
                    "с правом «Контент» (или задайте WB_CONTENT_TOKEN). Резервы по артикулам при этом учитываются "
                    "корректно; обновление склада на WB требует сопоставления."
                )
            return

        merged: dict[int, int] = {}
        for cid, amt in stocks_pairs:
            merged[cid] = merged.get(cid, 0) + amt

        stocks_list = [{"chrtId": cid, "amount": amt} for cid, amt in merged.items()]

        for i in range(0, len(stocks_list), _STOCKS_CHUNK):
            chunk = stocks_list[i : i + _STOCKS_CHUNK]
            payload = {"stocks": chunk}
            response = _wb_request(
                "PUT",
                f"{self.base_url}/api/v3/stocks/{self.warehouse_id}",
                headers=headers,
                json=payload,
                timeout=60,
            )
            if not response.ok:
                raise _wb_http_error(response)
