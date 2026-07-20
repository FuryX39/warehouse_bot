"""Сортировка PDF ярлыков Яндекс FBS по порядку строк Google Sheets."""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.fbs_labels_common import merge_label_pdfs, split_pdf_into_pages
from app.google_sheet_write import (
    WorksheetLookupError,
    open_google_spreadsheet,
    parse_worksheet_gid,
    resolve_worksheet,
)

# «59153319811 1/2» / «59153319811-1» / дробь «1/2»
_SHEET_KEY_RE = re.compile(
    r"(?P<order>\d{6,})(?:\s+(?P<place>\d+)\s*/\s*(?P<total>\d+))?"
)
_LABEL_ORDER_BOX_RE = re.compile(r"(?P<order>\d{6,})\s*-\s*(?P<box>\d+)")
_LABEL_FRACTION_RE = re.compile(r"(?<!\d)(?P<place>\d+)\s*/\s*(?P<total>\d+)(?!\d)")
_LABEL_ORDER_PLAIN_RE = re.compile(r"(?P<order>\d{8,})")


@dataclass(frozen=True)
class LabelKey:
    order_id: str
    place_index: int
    place_total: int

    def display(self) -> str:
        if self.place_total <= 1 and self.place_index <= 1:
            return self.order_id
        return f"{self.order_id} {self.place_index}/{self.place_total}"


@dataclass
class YandexLabelSortResult:
    pdf_bytes: bytes
    stats: dict[str, int | str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_sheet_label_key(raw: object) -> LabelKey | None:
    """Разобрать значение ячейки листа: «123456», «123456 1/2»."""
    text = str(raw or "").strip()
    if not text:
        return None
    match = _SHEET_KEY_RE.search(text)
    if not match:
        return None
    order_id = match.group("order")
    place_raw = match.group("place")
    total_raw = match.group("total")
    if place_raw and total_raw:
        place_index = int(place_raw)
        place_total = int(total_raw)
        if place_index < 1 or place_total < 1 or place_index > place_total:
            return None
        return LabelKey(order_id, place_index, place_total)
    return LabelKey(order_id, 1, 1)


def extract_sheet_label_keys(rows: Sequence[Sequence[Any]]) -> list[LabelKey]:
    """Ключи в порядке первого появления по строкам листа (слева направо)."""
    keys: list[LabelKey] = []
    seen: set[LabelKey] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        for cell in row:
            key = parse_sheet_label_key(cell)
            if key is None or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def extract_label_key_from_page_text(text: str) -> LabelKey | None:
    """Извлечь ключ ярлыка из текста страницы PDF."""
    normalized = str(text or "").replace("\u00a0", " ")
    if not normalized.strip():
        return None

    fractions = list(_LABEL_FRACTION_RE.finditer(normalized))
    order_boxes = list(_LABEL_ORDER_BOX_RE.finditer(normalized))

    place_index: int | None = None
    place_total: int | None = None
    if fractions:
        # Обычно на ярлыке одна дробь грузоместа («1/2»).
        place_index = int(fractions[0].group("place"))
        place_total = int(fractions[0].group("total"))
        if place_index < 1 or place_total < 1 or place_index > place_total:
            place_index = None
            place_total = None

    order_id: str | None = None
    if order_boxes:
        # Предпочитаем «order-box», совпадающий с индексом места.
        preferred = None
        for match in order_boxes:
            candidate = match.group("order")
            box = int(match.group("box"))
            if place_index is not None and box == place_index:
                preferred = candidate
                break
            if preferred is None:
                preferred = candidate
        order_id = preferred

    if order_id is None:
        plain = _LABEL_ORDER_PLAIN_RE.search(normalized)
        if plain:
            order_id = plain.group("order")

    if not order_id:
        return None
    if place_index is None or place_total is None:
        return LabelKey(order_id, 1, 1)
    return LabelKey(order_id, place_index, place_total)


def _page_text(pdf_page: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Установите pypdf для разбора ярлыков") from exc
    reader = PdfReader(io.BytesIO(pdf_page))
    if not reader.pages:
        return ""
    return reader.pages[0].extract_text() or ""


def sort_yandex_label_pdf(
    pdf_bytes: bytes,
    sheet_keys: Sequence[LabelKey],
) -> YandexLabelSortResult:
    """
    Собрать PDF в порядке sheet_keys.
    Лишние страницы исключаются; отсутствующие ключи — в warnings.
    """
    if not pdf_bytes:
        raise ValueError("PDF пустой")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Файл не является PDF")
    if not sheet_keys:
        raise ValueError("В листе не найдены номера заказов Яндекс")

    pages = split_pdf_into_pages(pdf_bytes)
    if not pages:
        raise ValueError("В PDF нет страниц")

    warnings: list[str] = []
    unused: dict[LabelKey, list[bytes]] = {}
    unparsed = 0
    for index, page in enumerate(pages, start=1):
        key = extract_label_key_from_page_text(_page_text(page))
        if key is None:
            unparsed += 1
            warnings.append(f"Страница {index}: не удалось распознать номер заказа")
            continue
        unused.setdefault(key, []).append(page)

    ordered_pages: list[bytes] = []
    matched = 0
    missing = 0
    for key in sheet_keys:
        bucket = unused.get(key)
        if bucket:
            ordered_pages.append(bucket.pop(0))
            matched += 1
            if not bucket:
                unused.pop(key, None)
        else:
            missing += 1
            warnings.append(f"Нет ярлыка для {key.display()}")

    extras = 0
    for key, bucket in sorted(unused.items(), key=lambda item: item[0].display()):
        for _ in bucket:
            extras += 1
            warnings.append(f"Лишний ярлык исключён: {key.display()}")

    if not ordered_pages:
        raise ValueError(
            "Не удалось сопоставить ни одного ярлыка с листом. "
            + ("; ".join(warnings[:5]) if warnings else "Проверьте PDF и ссылку на таблицу.")
        )

    merged = merge_label_pdfs(ordered_pages)
    if merged is None:
        raise RuntimeError("Не удалось объединить PDF (нужен pypdf)")

    stats = {
        "sheet_keys": len(sheet_keys),
        "pdf_pages": len(pages),
        "matched": matched,
        "missing": missing,
        "extras_dropped": extras,
        "unparsed_pages": unparsed,
        "output_pages": len(ordered_pages),
    }
    return YandexLabelSortResult(pdf_bytes=merged, stats=stats, warnings=warnings)


def load_sheet_label_keys(
    spreadsheet_url: str,
    *,
    credentials_path: str,
) -> tuple[list[LabelKey], str]:
    """Прочитать ключи с выбранной вкладки (#gid=... в URL). Возвращает (keys, title)."""
    url = str(spreadsheet_url or "").strip()
    if not url:
        raise ValueError("Укажите ссылку на Google Таблицу")
    creds = str(credentials_path or "").strip()
    if not creds:
        raise ValueError("Не настроен GOOGLE_SERVICE_ACCOUNT_FILE")

    gid = parse_worksheet_gid(url)
    try:
        spreadsheet = open_google_spreadsheet(url, creds)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc

    try:
        if gid is not None:
            worksheet = resolve_worksheet(spreadsheet, sheet_gid=gid)
        else:
            worksheets = spreadsheet.worksheets()
            if not worksheets:
                raise ValueError("В таблице нет вкладок")
            worksheet = worksheets[0]
    except WorksheetLookupError as exc:
        hint = ""
        if exc.available_titles:
            shown = ", ".join(exc.available_titles[:12])
            hint = f". Доступные листы: {shown}"
        raise ValueError(f"{exc}{hint}") from exc

    rows = worksheet.get_all_values()
    keys = extract_sheet_label_keys(rows)
    if not keys:
        raise ValueError(
            f"На листе «{worksheet.title}» не найдены номера заказов "
            "(ожидается вид «123456» или «123456 1/2»)"
        )
    return keys, str(worksheet.title or "")


def sort_yandex_labels_from_sheet(
    pdf_bytes: bytes,
    spreadsheet_url: str,
    *,
    credentials_path: str,
) -> YandexLabelSortResult:
    keys, sheet_title = load_sheet_label_keys(
        spreadsheet_url,
        credentials_path=credentials_path,
    )
    result = sort_yandex_label_pdf(pdf_bytes, keys)
    if sheet_title:
        result.stats["sheet_title"] = sheet_title
    return result


def warnings_header_json(warnings: Sequence[str], *, limit: int = 40) -> str:
    """ASCII JSON для HTTP-заголовка со списком предупреждений."""
    items = [str(w) for w in warnings[:limit]]
    payload = {"warnings": items, "total": len(warnings)}
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
