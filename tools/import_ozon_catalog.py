"""Импорт всех товаров Ozon в catalog_products. Существующие артикулы не трогаем."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ozon import OzonAdapter
from app.catalog_repository import (
    CatalogProduct,
    CatalogProductBarcode,
    CatalogRepository,
    _validate_code128,
)
from app.config import load_settings

LIST_LIMIT = 1000
ATTR_LIMIT = 100
PAUSE_SEC = 0.35
ATTR_DESCRIPTION = 4191
ATTR_COUNTRY = 4389
BARCODE_GROUP = "Озон"


def _strip_html(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = raw.replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _attr_values(item: dict[str, Any], attr_id: int) -> list[str]:
    out: list[str] = []
    for row in item.get("attributes") or []:
        if int(row.get("id") or 0) != attr_id:
            continue
        for value in row.get("values") or []:
            if isinstance(value, dict):
                text = str(value.get("value") or "").strip()
            else:
                text = str(value or "").strip()
            if text:
                out.append(text)
    return out


def _primary_image(item: dict[str, Any]) -> str:
    url = item.get("primary_image")
    if isinstance(url, str) and url.strip().startswith("http"):
        return url.strip()
    if isinstance(url, list) and url:
        first = url[0]
        if isinstance(first, str) and first.strip().startswith("http"):
            return first.strip()
    images = item.get("images") or []
    if isinstance(images, str) and images.strip().startswith("http"):
        return images.strip()
    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, str) and entry.strip().startswith("http"):
                return entry.strip()
            if isinstance(entry, dict):
                for key in ("url", "file_name", "src"):
                    val = str(entry.get(key) or "").strip()
                    if val.startswith("http"):
                        return val
    return ""


def _barcodes(item: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    barcodes = item.get("barcodes")
    if isinstance(barcodes, list):
        raw.extend(str(x).strip() for x in barcodes)
    barcode = item.get("barcode")
    if isinstance(barcode, str) and barcode.strip():
        raw.append(barcode.strip())
    out: list[str] = []
    seen: set[str] = set()
    for code in raw:
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _to_mm(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number == int(number):
        return str(int(number))
    return str(number)


def _weight_kg(item: dict[str, Any]) -> str:
    raw = item.get("weight")
    if raw in (None, ""):
        return ""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    unit = str(item.get("weight_unit") or "g").strip().lower()
    kg = number / 1000.0 if unit in {"g", "gr", "gram", "grams", "г", "гр"} else number
    text = f"{kg:.6f}".rstrip("0").rstrip(".")
    return text


def _paginate_attributes(adapter: OzonAdapter, visibility: str) -> list[dict[str, Any]]:
    last_id = ""
    items: list[dict[str, Any]] = []
    while True:
        try:
            body = adapter._post_json(
                "/v4/product/info/attributes",
                {
                    "filter": {"visibility": visibility},
                    "limit": ATTR_LIMIT,
                    "sort_dir": "ASC",
                    "last_id": last_id,
                },
                timeout=90,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if items and ("404" in msg or "item not found" in msg.lower()):
                print(f"  attributes {visibility}: конец списка ({len(items)} шт.)")
                break
            raise
        chunk = body.get("result")
        if not isinstance(chunk, list):
            nested = body.get("result")
            chunk = nested.get("items") if isinstance(nested, dict) else []
        chunk = [row for row in (chunk or []) if isinstance(row, dict)]
        items.extend(chunk)
        last_id = str(body.get("last_id") or "").strip()
        print(f"  attributes {visibility}: +{len(chunk)} (всего {len(items)}) last_id={bool(last_id)}")
        if not chunk or not last_id:
            break
        time.sleep(PAUSE_SEC)
    return items


def _default_ids(catalog: CatalogRepository) -> tuple[int | None, int | None]:
    meta = catalog.get_meta()
    unit_id = None
    for row in meta.get("units") or []:
        if row.get("is_default") or str(row.get("name") or "") == "шт":
            unit_id = int(row["id"])
            if row.get("is_default"):
                break
    marking_id = None
    for row in meta.get("marking_types") or []:
        if row.get("is_default"):
            marking_id = int(row["id"])
            break
    return unit_id, marking_id


def _existing_skus(catalog: CatalogRepository) -> set[str]:
    with Session(catalog.engine) as session:
        return {
            str(sku or "").strip().casefold()
            for sku in session.scalars(select(CatalogProduct.sku)).all()
            if str(sku or "").strip()
        }


def _existing_barcodes(catalog: CatalogRepository) -> set[str]:
    with Session(catalog.engine) as session:
        return {
            str(code or "").strip()
            for code in session.scalars(select(CatalogProductBarcode.barcode)).all()
            if str(code or "").strip()
        }


def _payload_from_ozon(
    item: dict[str, Any],
    *,
    code: str,
    unit_id: int | None,
    marking_id: int | None,
    used_barcodes: set[str],
) -> dict[str, Any]:
    sku = str(item.get("offer_id") or "").strip()
    name = str(item.get("name") or "").strip() or sku
    descriptions = _attr_values(item, ATTR_DESCRIPTION)
    countries = _attr_values(item, ATTR_COUNTRY)
    country = countries[0][:128] if countries and len(countries[0]) <= 64 else ""
    ozon_sku = item.get("sku")
    barcodes: list[dict[str, str]] = []
    for raw in _barcodes(item):
        if raw in used_barcodes:
            continue
        try:
            code_bc = _validate_code128(raw)
        except ValueError:
            continue
        if code_bc in used_barcodes:
            continue
        barcodes.append({"barcode": code_bc, "label": "", "group": BARCODE_GROUP})
    payload: dict[str, Any] = {
        "is_kit": False,
        "name": name,
        "sku": sku,
        "code": code,
        "external_code": str(ozon_sku or item.get("id") or "").strip(),
        "description": _strip_html("\n\n".join(descriptions))[:8192],
        "image_url": _primary_image(item),
        "country": country,
        "unit_id": unit_id,
        "weight": _weight_kg(item),
        "width_mm": _to_mm(item.get("width")),
        "height_mm": _to_mm(item.get("height")),
        "length_mm": _to_mm(item.get("depth")),
        "marking_type_id": marking_id,
        "components": [],
        "barcodes": barcodes,
    }
    return payload


def main() -> int:
    settings = load_settings()
    adapter = OzonAdapter(
        settings.ozon_client_id,
        settings.ozon_api_key,
        settings.ozon_warehouse_id,
    )
    if not adapter.is_configured():
        print("Ozon API не настроен", file=sys.stderr)
        return 1
    catalog = CatalogRepository(settings.db_url)
    catalog.init_schema()
    unit_id, marking_id = _default_ids(catalog)
    existing = _existing_skus(catalog)
    used_barcodes = _existing_barcodes(catalog)
    print(f"Каталог: {len(existing)} артикулов, {len(used_barcodes)} штрихкодов")

    by_offer: dict[str, dict[str, Any]] = {}
    for visibility in ("ALL", "ARCHIVED"):
        print(f"Загрузка Ozon visibility={visibility}")
        try:
            rows = _paginate_attributes(adapter, visibility)
        except Exception as exc:  # noqa: BLE001
            print(f"  пропуск {visibility}: {exc}")
            continue
        for item in rows:
            offer = str(item.get("offer_id") or "").strip()
            if offer:
                by_offer[offer] = item
    print(f"Уникальных offer_id на Ozon: {len(by_offer)}")

    created = 0
    skipped = 0
    failed = 0
    for offer, item in sorted(by_offer.items(), key=lambda kv: kv[0].casefold()):
        if offer.casefold() in existing:
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            print(f"SKIP empty name {offer}")
            skipped += 1
            continue
        code = catalog.generate_next_product_code()
        payload = _payload_from_ozon(
            item,
            code=code,
            unit_id=unit_id,
            marking_id=marking_id,
            used_barcodes=used_barcodes,
        )
        try:
            catalog.create_product(payload)
        except ValueError as exc:
            msg = str(exc)
            if "Штрихкод" in msg and "уже используется" in msg:
                payload["barcodes"] = []
                try:
                    catalog.create_product(payload)
                except Exception as exc2:  # noqa: BLE001
                    failed += 1
                    print(f"FAIL {offer}: {exc2}")
                    continue
            else:
                failed += 1
                print(f"FAIL {offer}: {exc}")
                continue
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {offer}: {exc}")
            continue
        existing.add(offer.casefold())
        for row in payload.get("barcodes") or []:
            code_bc = str(row.get("barcode") or "").strip()
            if code_bc:
                used_barcodes.add(code_bc)
        created += 1
        if created % 25 == 0:
            print(f"создано {created}, пропущено {skipped}, ошибок {failed}")
    print(f"Готово: создано {created}, пропущено {skipped}, ошибок {failed}, всего в каталоге {len(existing)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
