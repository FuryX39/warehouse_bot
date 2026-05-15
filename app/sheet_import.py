import csv
import io
from urllib.parse import quote

import requests


def _extract_sheet_id(url: str) -> str:
    marker = "/spreadsheets/d/"
    if marker not in url:
        raise ValueError("Некорректная ссылка Google Sheets.")
    tail = url.split(marker, maxsplit=1)[1]
    sheet_id = tail.split("/", maxsplit=1)[0].strip()
    if not sheet_id:
        raise ValueError("Не удалось извлечь ID таблицы из ссылки.")
    return sheet_id


def build_google_sheet_csv_url(url: str, *, sheet_name: str = "stocks") -> str:
    sheet_id = _extract_sheet_id(url)
    enc = quote(sheet_name, safe="")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={enc}"


def parse_sheet_stocks_csv(csv_text: str) -> tuple[dict[str, int], list[str]]:
    reader = csv.reader(io.StringIO(csv_text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("Таблица пустая.")

    header = [cell.strip().lower() for cell in rows[0]]
    sku_idx = None
    stock_idx = None
    for idx, name in enumerate(header):
        if name in {"sku", "артикул", "offer_id", "offersku"}:
            sku_idx = idx
        if name in {"stock", "остаток", "count", "qty", "quantity"}:
            stock_idx = idx

    data_rows = rows[1:]
    if sku_idx is None or stock_idx is None:
        sku_idx = 0
        stock_idx = 1
        data_rows = rows

    stocks_by_sku: dict[str, int] = {}
    warnings: list[str] = []
    for i, row in enumerate(data_rows, start=1):
        if len(row) <= max(sku_idx, stock_idx):
            warnings.append(f"Строка {i}: недостаточно колонок, пропущено.")
            continue
        sku = row[sku_idx].strip()
        stock_raw = row[stock_idx].strip()
        if not sku:
            warnings.append(f"Строка {i}: пустой SKU, пропущено.")
            continue
        try:
            stock = int(float(stock_raw.replace(",", ".")))
        except ValueError:
            warnings.append(f"Строка {i}: некорректный остаток '{stock_raw}', пропущено.")
            continue
        stocks_by_sku[sku] = max(stock, 0)
    return stocks_by_sku, warnings


def import_stocks_from_google_sheet(sheet_url: str) -> tuple[dict[str, int], list[str]]:
    csv_url = build_google_sheet_csv_url(sheet_url.strip(), sheet_name="stocks")
    response = requests.get(csv_url, timeout=30)
    response.raise_for_status()
    return parse_sheet_stocks_csv(response.text)


def parse_sheet_nomenclature_csv(csv_text: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Парсинг листа номенклатуры: sku, name, image_url (гибкие заголовки)."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("Таблица пустая.")

    header = [cell.strip().lower() for cell in rows[0]]
    sku_idx = None
    name_idx = None
    img_idx = None
    for idx, name in enumerate(header):
        if name in {"sku", "артикул", "offer_id", "offersku"}:
            sku_idx = idx
        if name in {"name", "title", "название", "наименование", "товар"}:
            name_idx = idx
        if name in {
            "image_url",
            "image",
            "url",
            "photo",
            "picture",
            "img",
            "фото",
            "картинка",
            "изображение",
            "ссылка_на_картинку",
            "ссылка на картинку",
            "picture_url",
        }:
            img_idx = idx

    data_rows = rows[1:]
    if sku_idx is None or name_idx is None:
        raise ValueError(
            "В шапке листа «nomenclature» не найдены колонки артикула и названия "
            "(например: sku и name / артикул и название). Колонка картинки опциональна."
        )

    items: dict[str, tuple[str, str]] = {}
    warnings: list[str] = []
    min_len = max(sku_idx, name_idx) + 1

    for i, row in enumerate(data_rows, start=1):
        if len(row) < min_len:
            warnings.append(f"Строка {i}: недостаточно колонок, пропущено.")
            continue
        sku = row[sku_idx].strip()
        if not sku:
            warnings.append(f"Строка {i}: пустой SKU, пропущено.")
            continue
        title = row[name_idx].strip() if name_idx < len(row) else ""
        img = ""
        if img_idx is not None and img_idx < len(row):
            img = row[img_idx].strip()
        items[sku] = (title, img)
    return items, warnings


def import_nomenclature_from_google_sheet(sheet_url: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Тот же spreadsheet URL, лист Google с именем «nomenclature»."""
    csv_url = build_google_sheet_csv_url(sheet_url.strip(), sheet_name="nomenclature")
    response = requests.get(csv_url, timeout=30)
    response.raise_for_status()
    return parse_sheet_nomenclature_csv(response.text)


def parse_sheet_movement_csv(csv_text: str) -> tuple[dict[str, int], list[str]]:
    """Парсинг листа movement: артикул и количество (положительное число в таблице)."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("Таблица пустая.")

    header = [cell.strip().lower() for cell in rows[0]]
    sku_idx = None
    qty_idx = None
    for idx, name in enumerate(header):
        if name in {"sku", "артикул", "offer_id", "offersku"}:
            sku_idx = idx
        if name in {
            "qty",
            "quantity",
            "count",
            "количество",
            "кол-во",
            "движение",
            "movement",
            "delta",
            "изменение",
            "stock",
            "остаток",
        }:
            qty_idx = idx

    data_rows = rows[1:]
    if sku_idx is None or qty_idx is None:
        sku_idx = 0
        qty_idx = 1
        data_rows = rows

    qty_by_sku: dict[str, int] = {}
    warnings: list[str] = []
    for i, row in enumerate(data_rows, start=1):
        if len(row) <= max(sku_idx, qty_idx):
            warnings.append(f"Строка {i}: недостаточно колонок, пропущено.")
            continue
        sku = row[sku_idx].strip()
        qty_raw = row[qty_idx].strip()
        if not sku:
            warnings.append(f"Строка {i}: пустой SKU, пропущено.")
            continue
        try:
            qty = int(float(qty_raw.replace(",", ".")))
        except ValueError:
            warnings.append(f"Строка {i}: некорректное количество '{qty_raw}', пропущено.")
            continue
        if qty <= 0:
            warnings.append(f"Строка {i}: количество должно быть > 0, пропущено.")
            continue
        qty_by_sku[sku] = qty_by_sku.get(sku, 0) + qty
    return qty_by_sku, warnings


def import_movement_from_google_sheet(sheet_url: str) -> tuple[dict[str, int], list[str]]:
    """Тот же spreadsheet URL, лист Google с именем «movement»."""
    csv_url = build_google_sheet_csv_url(sheet_url.strip(), sheet_name="movement")
    response = requests.get(csv_url, timeout=30)
    response.raise_for_status()
    return parse_sheet_movement_csv(response.text)
