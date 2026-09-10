"""Подтянуть primary_image из Ozon Seller API в catalog_products.image_url."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.ozon import OzonAdapter
from app.catalog_repository import CatalogRepository
from app.config import load_settings

CHUNK = 100
PAUSE_SEC = 0.35


def _primary_image(item: dict[str, Any]) -> str:
    url = item.get("primary_image")
    if isinstance(url, str) and url.strip():
        return url.strip()
    if isinstance(url, list) and url:
        first = url[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    images = item.get("images") or item.get("image") or []
    if isinstance(images, str) and images.strip():
        return images.strip()
    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
            if isinstance(entry, dict):
                for key in ("url", "file_name", "src"):
                    val = str(entry.get(key) or "").strip()
                    if val:
                        return val
    return ""


def _info_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if isinstance(items, list):
        return [row for row in items if isinstance(row, dict)]
    result = body.get("result")
    if isinstance(result, dict):
        nested = result.get("items")
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    return []


def fetch_images_by_offer(adapter: OzonAdapter, offer_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    unique = list(dict.fromkeys(str(x).strip() for x in offer_ids if str(x).strip()))
    for start in range(0, len(unique), CHUNK):
        chunk = unique[start : start + CHUNK]
        body = adapter._post_json(
            "/v3/product/info/list",
            {"offer_id": chunk, "product_id": [], "sku": []},
            timeout=90,
        )
        for item in _info_items(body):
            offer = str(item.get("offer_id") or "").strip()
            url = _primary_image(item)
            if offer and url:
                out[offer] = url
        if start + CHUNK < len(unique):
            time.sleep(PAUSE_SEC)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Заполнить catalog image_url из Ozon primary_image")
    parser.add_argument(
        "--only-empty",
        action="store_true",
        help="Не перезаписывать товары, у которых image_url уже задан",
    )
    args = parser.parse_args()
    settings = load_settings()
    adapter = OzonAdapter(
        settings.ozon_client_id,
        settings.ozon_api_key,
        settings.ozon_warehouse_id,
    )
    if not adapter.is_configured():
        print("Ozon API не настроен (OZON_CLIENT_ID / OZON_API_KEY)", file=sys.stderr)
        return 1
    catalog = CatalogRepository(settings.db_url)
    products = catalog.list_products({})
    skus = [str(p.sku or "").strip() for p in products if str(p.sku or "").strip()]
    empty = sum(1 for p in products if not str(p.image_url or "").strip())
    print(f"Каталог: {len(products)} товаров, без картинки: {empty}")
    print(f"Запрос к Ozon: {len(skus)} offer_id, пачки по {CHUNK}")
    found = fetch_images_by_offer(adapter, skus)
    print(f"Ozon вернул картинку: {len(found)}")
    updated = catalog.apply_image_urls_by_sku(found, only_empty=bool(args.only_empty))
    print(f"Обновлено в БД: {updated}")
    missing = [
        sku
        for sku in skus
        if sku.casefold() not in {key.casefold() for key in found}
    ]
    print(f"Нет на Ozon / без фото: {len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
