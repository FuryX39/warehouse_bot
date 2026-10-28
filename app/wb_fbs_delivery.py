"""Передача поставок WB FBS в доставку: пункт сдачи, дата, QR."""

from __future__ import annotations

import base64
import re
from datetime import date, datetime
from typing import Any

from app.adapters.wildberries import WildberriesAdapter
from app.fbs_labels_common import merge_label_pdfs
from app.fbs_packing_repository import MARKETPLACE_WB, FbsPackingJobRow
from app.wb_fbs_labels import png_bytes_to_label_pdf

SHIPPING_SELF = "selfShipping"
SHIPPING_TRANSPORT = "transportCompany"
SHIPPING_TYPES = (
    {"id": SHIPPING_SELF, "title": "Самостоятельно"},
    {"id": SHIPPING_TRANSPORT, "title": "Транспортной компанией"},
)

DEFAULT_WB_FBS_SHIPPING_POINTS = (
    {
        "key": "oby_3307",
        "city": "Обь",
        "title": "Обь, 3307 км, д. 30, к. 2",
        "needles": ("3307", "30"),
    },
    {
        "key": "nsk_dokuchaeva",
        "city": "Новосибирск",
        "title": "Новосибирск, улица Докучаева, 1/2",
        "needles": ("докучаева", "1 2"),
    },
)

_WB_GI_RE = re.compile(r"WB-GI-[A-Za-z0-9_-]+", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ALREADY_DELIVERED = (
    "already delivered",
    "already in delivery",
    "supplyalreadydelivered",
    "уже передана",
    "уже в доставке",
)


def job_wb_supply_ids(job: FbsPackingJobRow | dict[str, Any] | None) -> list[str]:
    if job is None:
        return []
    if isinstance(job, dict):
        texts = [job.get("supply_id"), job.get("sheet_title")]
    else:
        texts = [getattr(job, "supply_id", ""), getattr(job, "sheet_title", "")]
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _WB_GI_RE.findall(str(text or "")):
            key = match.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(match)
    return out


def normalize_shipping_dt(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Укажите дату сдачи в формате ГГГГ-ММ-ДД") from exc
    return parsed.isoformat()


def normalize_shipping_type(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "self": SHIPPING_SELF,
        "selfshipping": SHIPPING_SELF,
        "самостоятельно": SHIPPING_SELF,
        "tk": SHIPPING_TRANSPORT,
        "transport": SHIPPING_TRANSPORT,
        "transportcompany": SHIPPING_TRANSPORT,
        "транспортной": SHIPPING_TRANSPORT,
        "транспортной компанией": SHIPPING_TRANSPORT,
    }
    mapped = aliases.get(text.casefold(), text)
    if mapped not in (SHIPPING_SELF, SHIPPING_TRANSPORT):
        raise ValueError("Выберите способ: самостоятельно или транспортной компанией")
    return mapped


def normalize_waybill_uuid(value: object) -> str:
    text = str(value or "").strip()
    if _UUID_RE.match(text):
        return text.lower()
    compact = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(compact) == 32:
        hex_text = compact.lower()
        return f"{hex_text[:8]}-{hex_text[8:12]}-{hex_text[12:16]}-{hex_text[16:20]}-{hex_text[20:]}"
    raise ValueError("Укажите УИД ТРН в формате UUID")


def default_shipping_date() -> str:
    return date.today().isoformat()


def _fold_addr(text: str) -> str:
    folded = str(text or "").casefold().replace("ё", "е")
    folded = re.sub(r"[^\w\s]+", " ", folded, flags=re.UNICODE)
    return re.sub(r"\s+", " ", folded).strip()


def _point_blob(point: dict[str, Any]) -> str:
    return _fold_addr(
        " ".join(
            str(point.get(key) or "")
            for key in ("city", "address", "name", "office_type")
        )
    )


def _point_score(point: dict[str, Any], needles: tuple[str, ...]) -> int:
    blob = _point_blob(point)
    score = 0
    for needle in needles:
        folded = _fold_addr(needle)
        if folded and folded in blob:
            score += 1
    return score


def match_default_shipping_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сопоставляет ответ WB с двумя пунктами Новосибирска по умолчанию."""
    resolved: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    for preset in DEFAULT_WB_FBS_SHIPPING_POINTS:
        best: dict[str, Any] | None = None
        best_score = 0
        for point in points:
            try:
                point_id = int(point.get("id") or point.get("shipping_point_id") or 0)
            except (TypeError, ValueError):
                point_id = 0
            if point_id <= 0 or point_id in used_ids:
                continue
            score = _point_score(point, tuple(preset["needles"]))
            if score <= 0:
                continue
            if score > best_score:
                best = point
                best_score = score
        point_id = None
        address = str(preset["title"])
        city = str(preset["city"])
        if best is not None:
            try:
                point_id = int(best.get("id") or 0)
            except (TypeError, ValueError):
                point_id = None
            if point_id:
                used_ids.add(point_id)
            address = str(best.get("address") or best.get("name") or preset["title"]).strip()
            city = str(best.get("city") or preset["city"]).strip()
        resolved.append(
            {
                "key": preset["key"],
                "title": preset["title"],
                "city": city,
                "shipping_point_id": point_id,
                "address": address,
                "available": bool(point_id),
            }
        )
    return resolved


def _require_wb_job(job: FbsPackingJobRow | None) -> FbsPackingJobRow:
    if job is None:
        raise ValueError("Задание не найдено")
    marketplace = str(getattr(job, "marketplace", "") or "").strip().lower()
    if marketplace != MARKETPLACE_WB:
        raise ValueError("Передача в доставку доступна только для заданий Wildberries FBS")
    if str(getattr(job, "status", "") or "") == "cancelled":
        raise ValueError("Отменённое задание нельзя передать в доставку")
    return job


def _cargo_types(adapter: WildberriesAdapter, supply_id: str) -> list[int]:
    found: int | None = None
    try:
        info = adapter.fetch_supply(supply_id)
        raw = info.get("cargoType") if isinstance(info, dict) else None
        if raw is not None:
            found = int(raw)
    except (TypeError, ValueError, Exception):
        found = None
    order = [1, 2, 3]
    if found in order:
        return [found, *[item for item in order if item != found]]
    return order


def _load_city_points(
    adapter: WildberriesAdapter,
    *,
    supply_id: str,
    city: str,
    shipping_dt: str,
    cargo_types: list[int],
) -> list[dict[str, Any]]:
    try:
        points = adapter.list_supply_shipping_points(
            supply_id,
            city=city,
            shipping_dt=shipping_dt,
        )
        if points:
            return points
    except Exception:
        points = []
    collected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for cargo_type in cargo_types:
        try:
            batch = adapter.list_fbs_shipping_points(city=city, cargo_type=cargo_type)
        except Exception:
            continue
        for point in batch:
            try:
                point_id = int(point.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if point_id in seen:
                continue
            seen.add(point_id)
            collected.append(point)
        if collected:
            break
    return collected


def load_job_delivery_meta(adapter: WildberriesAdapter, job: FbsPackingJobRow) -> dict[str, Any]:
    job = _require_wb_job(job)
    supply_ids = job_wb_supply_ids(job)
    if not supply_ids:
        raise ValueError("В задании нет поставки Wildberries")
    shipping_dt = default_shipping_date()
    cargo_types = _cargo_types(adapter, supply_ids[0])
    cities = []
    for preset in DEFAULT_WB_FBS_SHIPPING_POINTS:
        city = str(preset["city"])
        if city not in cities:
            cities.append(city)
    all_points: list[dict[str, Any]] = []
    seen: set[int] = set()
    for city in cities:
        for point in _load_city_points(
            adapter,
            supply_id=supply_ids[0],
            city=city,
            shipping_dt=shipping_dt,
            cargo_types=cargo_types,
        ):
            try:
                point_id = int(point.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if point_id in seen:
                continue
            seen.add(point_id)
            all_points.append(point)
    points = match_default_shipping_points(all_points)
    return {
        "job_id": int(job.id),
        "supply_ids": supply_ids,
        "shipping_date": shipping_dt,
        "points": points,
        "shipping_types": [dict(item) for item in SHIPPING_TYPES],
    }


def _raise_batch_errors(payload: dict[str, Any], *, action: str) -> None:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return
    errors: list[str] = []
    for row in results:
        if not isinstance(row, dict) or row.get("success"):
            continue
        sid = str(row.get("supplyId") or row.get("id") or "").strip() or "поставка"
        err = row.get("error")
        if isinstance(err, dict):
            message = str(
                err.get("message") or err.get("detail") or err.get("title") or err.get("code") or ""
            ).strip()
        else:
            message = str(err or "").strip()
        errors.append(f"{sid}: {message or 'ошибка WB'}")
    if errors:
        raise ValueError(f"{action}: " + "; ".join(errors))


def _already_delivered(exc: BaseException) -> bool:
    text = str(exc).casefold().replace(" ", "")
    return any(token.replace(" ", "") in text for token in _ALREADY_DELIVERED)


def deliver_job_supplies(
    adapter: WildberriesAdapter,
    job: FbsPackingJobRow,
    *,
    shipping_point_id: object,
    shipping_dt: object,
    shipping_type: object,
    waybill_uuid: object = "",
    point_key: object = "",
) -> tuple[bytes, list[str]]:
    job = _require_wb_job(job)
    supply_ids = job_wb_supply_ids(job)
    if not supply_ids:
        raise ValueError("В задании нет поставки Wildberries")
    date_text = normalize_shipping_dt(shipping_dt)
    type_text = normalize_shipping_type(shipping_type)
    try:
        point_id = int(shipping_point_id)
    except (TypeError, ValueError):
        point_id = 0
    key = str(point_key or "").strip()
    if point_id <= 0:
        meta = load_job_delivery_meta(adapter, job)
        for point in meta["points"]:
            if key and point.get("key") == key and point.get("shipping_point_id"):
                point_id = int(point["shipping_point_id"])
                break
            if not key and point.get("available"):
                point_id = int(point["shipping_point_id"])
                break
    if point_id <= 0:
        raise ValueError("Выберите пункт сдачи")
    trn = ""
    if type_text == SHIPPING_TRANSPORT:
        trn = normalize_waybill_uuid(waybill_uuid)

    method_payload = [
        {
            "supplyId": sid,
            "shippingDt": date_text,
            "shippingPointId": point_id,
            "shippingType": type_text,
        }
        for sid in supply_ids
    ]
    method_result = adapter.set_supplies_shipping_method(method_payload)
    _raise_batch_errors(method_result, action="Параметры отгрузки")
    if trn:
        waybill_result = adapter.set_supplies_waybill(
            [{"supplyId": sid, "waybillUuid": trn} for sid in supply_ids]
        )
        _raise_batch_errors(waybill_result, action="УИД ТРН")

    delivered: list[str] = []
    warnings: list[str] = []
    for sid in supply_ids:
        try:
            adapter.deliver_supply(sid)
            delivered.append(sid)
        except Exception as exc:  # noqa: BLE001
            if _already_delivered(exc):
                delivered.append(sid)
                continue
            raise ValueError(f"Не удалось передать {sid} в доставку: {exc}") from exc

    pdf_parts: list[bytes] = []
    for sid in delivered:
        try:
            sticker = adapter.fetch_supply_barcode(sid)
            png = base64.b64decode(sticker["png_b64"])
            pdf_parts.append(png_bytes_to_label_pdf(png))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{sid}: {exc}")
    if not pdf_parts:
        detail = "; ".join(warnings) if warnings else "WB не вернул QR поставок"
        raise ValueError(
            "Поставки переданы в доставку, но QR скачать не удалось: " + detail
        )
    pdf = merge_label_pdfs(pdf_parts)
    if not pdf:
        raise ValueError("Не удалось собрать PDF с QR поставок")
    return pdf, delivered
