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
    # in_process_at из API: для порядка как в ЛК Ozon (сверху старые, снизу новые).
    in_process_at_ts: int = 0


class OzonAdapter(MarketplaceAdapter):
    name = "ozon"
    base_url = "https://api-seller.ozon.ru"
    # Полный снимок резервируемых отправлений — можно снимать резервы при отмене/смене статуса.
    supports_reserve_reconciliation = True
    # Для Ozon дельта = тот же full snapshot (см. fetch_reservations_delta),
    # поэтому reconcile нужно делать на каждом цикле, иначе отменённые заказы
    # могут висеть в state=added до следующего full-окна и давать лишние минусы.
    reconcile_on_delta = True

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

    def _post_json(self, path: str, payload: dict, *, timeout: int = 60) -> dict:
        if not self.is_configured():
            raise RuntimeError("Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)")
        response = requests.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = (response.text or "").strip()
            if body:
                raise requests.HTTPError(f"{exc}; body={body[:1000]}") from exc
            raise
        return response.json() if response.content else {}

    def _get_bytes(self, path: str, *, timeout: int = 90) -> bytes:
        if not self.is_configured():
            raise RuntimeError("Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)")
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _to_ozon_datetime(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def fbo_cluster_list(self, payload: dict | None = None) -> dict:
        body = dict(payload or {})
        if body.get("cluster_type"):
            return self._post_json("/v1/cluster/list", body)

        # Ozon made cluster_type validation strict. Different documentation mirrors
        # mention different enum values, so try the known FBO-related variants and
        # keep the first successful response.
        candidates = (
            "CLUSTER_TYPE_OZON",
            "CLUSTER_TYPE_FBO",
            "CLUSTER_TYPE_LOGISTIC",
            "CLUSTER_TYPE_UNKNOWN",
        )
        errors: list[str] = []
        for cluster_type in candidates:
            attempt = {**body, "cluster_type": cluster_type}
            try:
                data = self._post_json("/v1/cluster/list", attempt)
            except requests.HTTPError as exc:
                errors.append(f"{cluster_type}: {exc}")
                continue
            if isinstance(data, dict):
                data.setdefault("_request_cluster_type", cluster_type)
            return data
        raise RuntimeError("Не удалось получить кластеры Ozon; " + " | ".join(errors[:4]))

    def fbo_warehouse_list(self, payload: dict | None = None) -> dict:
        return self._post_json("/v1/warehouse/fbo/list", payload or {})

    def fbo_draft_create(self, payload: dict) -> dict:
        return self._post_json("/v1/draft/create", payload, timeout=90)

    def fbo_draft_create_info(self, payload: dict) -> dict:
        return self._post_json("/v1/draft/create/info", payload)

    def fbo_timeslot_info(self, payload: dict) -> dict:
        return self._post_json("/v1/draft/timeslot/info", payload)

    def fbo_draft_supply_create(self, payload: dict) -> dict:
        return self._post_json("/v1/draft/supply/create", payload, timeout=90)

    def fbo_draft_supply_create_status(self, payload: dict) -> dict:
        return self._post_json("/v1/draft/supply/create/status", payload)

    def fbo_supply_order_list(self, payload: dict) -> dict:
        return self._post_json("/v3/supply-order/list", payload)

    def fbo_supply_order_get(self, payload: dict) -> dict:
        return self._post_json("/v3/supply-order/get", payload)

    def fbo_supply_order_bundle(self, payload: dict) -> dict:
        return self._post_json("/v1/supply-order/bundle", payload)

    def fbo_cargoes_create(self, payload: dict) -> dict:
        return self._post_json("/v1/cargoes/create", payload, timeout=90)

    def fbo_cargoes_create_info(self, payload: dict) -> dict:
        return self._post_json("/v2/cargoes/create/info", payload)

    def fbo_cargoes_delete(self, payload: dict) -> dict:
        return self._post_json("/v1/cargoes/delete", payload, timeout=90)

    def fbo_cargoes_get(self, inner_supply_ids: list[int]) -> dict:
        ids = [int(x) for x in inner_supply_ids if int(x) > 0][:100]
        if not ids:
            raise ValueError("Укажите supply_id поставки Ozon")
        return self._post_json(
            "/v2/cargoes/get",
            {"supplies": [{"supply_id": sid} for sid in ids]},
            timeout=120,
        )

    def fbo_cargoes_rules_get(self, payload: dict) -> dict:
        return self._post_json("/v1/cargoes/rules/get", payload)

    def fbo_cargo_labels_create(self, payload: dict) -> dict:
        return self._post_json("/v1/cargoes-label/create", payload, timeout=90)

    def fbo_cargo_labels_get(self, payload: dict) -> dict:
        return self._post_json("/v1/cargoes-label/get", payload)

    def fbo_cargo_labels_file(self, file_guid: str) -> bytes:
        guid = str(file_guid or "").strip()
        if not guid:
            raise ValueError("file_guid пуст")
        return self._get_bytes(f"/v1/cargoes-label/file/{guid}", timeout=120)

    @staticmethod
    def _in_process_at_ts(posting: dict) -> int:
        raw = posting.get("in_process_at")
        if not raw:
            return 0
        s = str(raw).strip()
        if not s:
            return 0
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

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
        которые реально готовы к отгрузке со склада:
        awaiting_deliver.
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
        ship_statuses: tuple[str, ...] = OZON_AWAITING_SHIPMENT_STATUSES
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
                    in_ts = self._in_process_at_ts(posting)
                    prev = by_posting.get(posting_number)
                    if prev is not None and prev.in_process_at_ts > 0:
                        in_ts = min(in_ts, prev.in_process_at_ts) if in_ts > 0 else prev.in_process_at_ts
                    by_posting[posting_number] = OzonFbsPosting(
                        posting_number=posting_number,
                        status=status,
                        lines=lines,
                        in_process_at_ts=in_ts,
                    )
                if len(postings) < 1000:
                    break
                offset += 1000

        return sorted(by_posting.values(), key=lambda p: (p.in_process_at_ts, p.posting_number))

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

    def fetch_package_label_by_posting(
        self,
        posting_numbers: list[str],
        *,
        chunk_size: int = _PACKAGE_LABEL_MAX_POSTINGS,
    ) -> tuple[dict[str, bytes], list[str]]:
        """
        Этикетка на каждое отправление. PDF из API режется по страницам
        (порядок страниц = порядок posting_number в запросе).
        """
        from app.fbs_labels_common import split_pdf_into_pages

        parts, warnings = self.fetch_package_label_pdf_parts(
            posting_numbers,
            chunk_size=chunk_size,
        )
        unique = list(dict.fromkeys(str(p).strip() for p in posting_numbers if str(p).strip()))
        by_posting: dict[str, bytes] = {}
        chunk_size = max(1, min(int(chunk_size), _PACKAGE_LABEL_MAX_POSTINGS))
        part_idx = 0
        for start in range(0, len(unique), chunk_size):
            chunk = unique[start : start + chunk_size]
            if part_idx >= len(parts):
                warnings.append(f"Нет PDF для отправлений: {', '.join(chunk)}")
                break
            _, pdf = parts[part_idx]
            part_idx += 1
            pages = split_pdf_into_pages(pdf)
            if len(pages) == len(chunk):
                for pn, page_pdf in zip(chunk, pages):
                    by_posting[pn] = page_pdf
            elif len(chunk) == 1 and len(pages) >= 1:
                by_posting[chunk[0]] = pages[0]
            else:
                warnings.append(
                    f"Этикетки {chunk[0]}…: в PDF {len(pages)} стр., ожидалось {len(chunk)} — "
                    "порядок этикеток может не совпасть с листом."
                )
                for i, pn in enumerate(chunk):
                    if i < len(pages):
                        by_posting[pn] = pages[i]
        return by_posting, warnings

    def _resolve_product_ids(self, offer_ids: list[str], headers: dict[str, str]) -> dict[str, int]:
        """offer_id → product_id для POST /v2/products/stocks (Ozon надёжнее принимает оба поля)."""
        out: dict[str, int] = {}
        ids = [str(x).strip() for x in offer_ids if str(x).strip()]
        if not ids:
            return out
        chunk_size = 100
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            response = requests.post(
                f"{self.base_url}/v3/product/info/list",
                headers=headers,
                json={"offer_id": chunk, "product_id": [], "sku": []},
                timeout=30,
            )
            response.raise_for_status()
            items = response.json().get("items") or []
            if not items and isinstance(response.json().get("result"), dict):
                items = response.json()["result"].get("items") or []
            for item in items:
                offer = str(item.get("offer_id") or "").strip()
                pid = item.get("id") or item.get("product_id")
                if offer and pid is not None:
                    try:
                        out[offer] = int(pid)
                    except (TypeError, ValueError):
                        continue
        return out

    @staticmethod
    def _parse_stocks_update_result(body: dict) -> tuple[int, list[str]]:
        """Считает успешные обновления и тексты ошибок из ответа /v2/products/stocks."""
        updated = 0
        errors: list[str] = []
        rows = body.get("result")
        if rows is None:
            rows = body.get("results") or []
        if not isinstance(rows, list):
            return 0, [f"неожиданный ответ API: {body!r}"[:500]]
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("updated") is True:
                updated += 1
                continue
            offer = str(row.get("offer_id") or "").strip()
            for err in row.get("errors") or []:
                if isinstance(err, dict):
                    msg = str(err.get("message") or err.get("code") or err).strip()
                else:
                    msg = str(err).strip()
                if msg:
                    prefix = f"{offer}: " if offer else ""
                    errors.append(prefix + msg)
            if not row.get("errors") and row.get("updated") is not True:
                errors.append(f"{offer or '?'}: не обновлено (updated=false)")
        return updated, errors

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        if not self.is_configured() or not available_stock_by_sku:
            return
        if not is_value_configured(self.warehouse_id):
            raise RuntimeError("Set OZON_WAREHOUSE_ID in .env for Ozon stock sync")
        try:
            warehouse_id = int(str(self.warehouse_id).strip())
        except ValueError as exc:
            raise RuntimeError(
                f"OZON_WAREHOUSE_ID должен быть числом (id склада FBS из /v1/warehouse/list), сейчас: {self.warehouse_id!r}"
            ) from exc

        headers = self._headers()
        quantities: dict[str, int] = {}
        for sku, available in available_stock_by_sku.items():
            key = str(sku).strip()
            if not key:
                continue
            quantities[key] = max(int(available), 0)

        if not quantities:
            return

        offer_ids = list(quantities.keys())
        product_ids = self._resolve_product_ids(offer_ids, headers)
        unknown = [oid for oid in offer_ids if oid not in product_ids]
        if unknown:
            logger.warning(
                "Ozon stock: %s offer_id не найдены в каталоге (первые 10): %s",
                len(unknown),
                unknown[:10],
            )

        stocks: list[dict] = []
        for offer_id, qty in quantities.items():
            pid = product_ids.get(offer_id)
            if pid is None:
                continue
            row: dict = {
                "offer_id": offer_id,
                "product_id": pid,
                "stock": qty,
                "warehouse_id": warehouse_id,
            }
            stocks.append(row)

        if not stocks:
            raise RuntimeError(
                "Ozon stock: ни один артикул не сопоставлен с каталогом Ozon "
                "(проверьте, что product_stocks.sku = offer_id в ЛК Ozon)"
            )

        total_updated = 0
        all_errors: list[str] = []
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
            updated, errs = self._parse_stocks_update_result(response.json())
            total_updated += updated
            all_errors.extend(errs)

        logger.info(
            "Ozon stock push: отправлено %s поз., обновлено %s, ошибок %s, пропущено (нет в каталоге) %s",
            len(stocks),
            total_updated,
            len(all_errors),
            len(unknown),
        )
        if total_updated == 0:
            sample = "; ".join(all_errors[:5])
            raise RuntimeError(
                "Ozon stock: API не обновил ни одной позиции"
                + (f" ({sample})" if sample else "")
            )
        # Частичный успех для Ozon опасен: часть SKU останется со старыми остатками.
        # Считаем такой пуш ошибкой, чтобы координатор НЕ обновил hash и повторил попытку.
        if all_errors or total_updated < len(stocks):
            sample = "; ".join(all_errors[:5]) if all_errors else "часть строк не обновлена без деталей"
            raise RuntimeError(
                f"Ozon stock: частичный пуш {total_updated}/{len(stocks)}; {sample}"
            )
