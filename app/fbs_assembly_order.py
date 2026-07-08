"""Порядок строк FBS-списка по сборочному листу ТСД (лист assembly в bot_table / DEFAULT_STOCKS_SHEET_URL)."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Sequence

from app.google_sheet_write import WorksheetLookupError, open_google_spreadsheet, resolve_worksheet

_INVISIBLE_RE = re.compile(r"[\u200b-\u200d\ufeff]")
_OFFER_ID_FULL_RE = re.compile(r"^(SS[A-Z0-9]+)$", re.IGNORECASE)
_OFFER_ID_EMBEDDED_RE = re.compile(r"\b(SS[A-Z0-9]+)\b", re.IGNORECASE)
_SKIP_ROW_MARKERS = ("итого", "всего", "сумма")


def _norm_sku_raw(sku: str) -> str:
    """Единая нормализация артикула: пробелы, NBSP, невидимые символы, кавычки из Google Sheets."""
    s = unicodedata.normalize("NFKC", str(sku or ""))
    s = s.replace("\u00a0", " ")
    s = _INVISIBLE_RE.sub("", s)
    return s.strip().strip("'\"")


def sku_match_key(sku: str) -> str:
    """Ключ для сопоставления артикулов assembly ↔ FBS (регистр не важен)."""
    return _norm_sku_raw(sku).casefold()


def looks_like_offer_id(sku: str) -> bool:
    return bool(_OFFER_ID_FULL_RE.match(_norm_sku_raw(sku)))


def extract_offer_id_from_cell(value: str) -> str:
    """
    Артикул из ячейки assembly.
    Поддерживает SS278 в отдельной колонке и внутри названия («Товар SS278 …»).
    """
    raw = _norm_sku_raw(value)
    if not raw:
        return ""
    if looks_like_offer_id(raw):
        return raw.upper()
    match = _OFFER_ID_EMBEDDED_RE.search(raw)
    if match:
        return match.group(1).upper()
    return raw


@dataclass(frozen=True)
class AssemblySheetEntry:
    sku: str
    place: str
    sort_index: int


def _norm_cell(row: Sequence[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _detect_assembly_columns(header: Sequence[str]) -> tuple[int, int]:
    """Колонки артикула и ячейки; «Артикул» важнее «Номенклатура» (там часто название)."""
    sku_idx = 0
    place_idx = 2
    sku_priority: int | None = None
    lowered = [str(c or "").strip().casefold() for c in header]
    for i, name in enumerate(lowered):
        if not name:
            continue
        if "ячей" in name or name in {"место", "ячейка", "cell"}:
            place_idx = i
        elif any(k in name for k in ("артикул", "offer_id", "offer", "sku")):
            sku_idx = i
            sku_priority = 0
        elif "номенклат" in name and sku_priority is None:
            sku_idx = i
            sku_priority = 1
    return sku_idx, place_idx


def _looks_like_assembly_header(row: Sequence[str]) -> bool:
    if not row:
        return False
    joined = " ".join(str(c or "").strip().casefold() for c in row if str(c or "").strip())
    return any(k in joined for k in ("номенклат", "артикул", "ячей", "sku", "количество"))


def _assembly_data_start_and_columns(values: list[list[str]]) -> tuple[int, int, int]:
    for i, row in enumerate(values[:15]):
        if _looks_like_assembly_header(row):
            sku_idx, place_idx = _detect_assembly_columns(row)
            return i + 1, sku_idx, place_idx
    return 0, 0, 2


def _sku_from_assembly_row(row: Sequence[str], sku_idx: int) -> str:
    primary = extract_offer_id_from_cell(_norm_cell(row, sku_idx))
    if looks_like_offer_id(primary):
        return primary
    embedded = _OFFER_ID_EMBEDDED_RE.search(primary)
    if embedded:
        return embedded.group(1).upper()
    for i, cell in enumerate(row):
        if i == sku_idx:
            continue
        candidate = extract_offer_id_from_cell(cell)
        if looks_like_offer_id(candidate):
            return candidate
        match = _OFFER_ID_EMBEDDED_RE.search(candidate)
        if match:
            return match.group(1).upper()
    return primary


def _is_assembly_data_row(sku: str) -> bool:
    if not sku:
        return False
    low = sku.casefold()
    if any(marker in low for marker in _SKIP_ROW_MARKERS):
        return False
    if looks_like_offer_id(sku):
        return True
    return bool(_OFFER_ID_EMBEDDED_RE.search(sku))


def parse_assembly_sheet_values(values: list[list[str]]) -> list[AssemblySheetEntry]:
    """Разбор листа assembly: артикул + ячейка, порядок строк = маршрут ТСД."""
    if not values:
        return []
    start, sku_idx, place_idx = _assembly_data_start_and_columns(values)

    out: list[AssemblySheetEntry] = []
    for row in values[start:]:
        sku = _sku_from_assembly_row(row, sku_idx)
        if not _is_assembly_data_row(sku):
            continue
        place = _norm_cell(row, place_idx)
        out.append(AssemblySheetEntry(sku=sku, place=place, sort_index=len(out)))
    return out


def assembly_sku_keys(entries: Sequence[AssemblySheetEntry]) -> set[str]:
    return {sku_match_key(entry.sku) for entry in entries if entry.sku}


def reorder_ozon_fbs_list_rows(
    list_rows: Sequence,
    entries: Sequence[AssemblySheetEntry],
    *,
    row_factory,
) -> list:
    """
    Порядок FBS = обход assembly сверху вниз.
    Каждая строка assembly «забирает» следующую FBS-строку с тем же артикулом.
    Нераспределённые строки — в конце, в исходном порядке.
    """
    if not list_rows:
        return []
    if not entries:
        return [
            row_factory(i + 1, row.posting_number, row.sku, row.quantity, row.status)
            for i, row in enumerate(list_rows)
        ]

    pools: dict[str, deque[int]] = defaultdict(deque)
    for i, row in enumerate(list_rows):
        pools[sku_match_key(row.sku)].append(i)

    placed_indices: list[int] = []
    for entry in entries:
        key = sku_match_key(entry.sku)
        if pools[key]:
            placed_indices.append(pools[key].popleft())

    placed_set = set(placed_indices)
    for i in range(len(list_rows)):
        if i not in placed_set:
            placed_indices.append(i)

    flat = [list_rows[i] for i in placed_indices]
    return [
        row_factory(i + 1, row.posting_number, row.sku, row.quantity, row.status)
        for i, row in enumerate(flat)
    ]


def load_assembly_entries_from_google_sheet(
    spreadsheet_url: str,
    credentials_path: str,
    *,
    sheet_name: str = "assembly",
    sheet_gid: int | None = None,
) -> list[AssemblySheetEntry]:
    sh = open_google_spreadsheet(spreadsheet_url, credentials_path)
    ws = resolve_worksheet(
        sh,
        sheet_name=str(sheet_name or "assembly").strip() or "assembly",
        sheet_gid=sheet_gid,
    )
    return parse_assembly_sheet_values(ws.get_all_values())


def apply_assembly_order_to_ozon_rows(
    list_rows: list,
    *,
    default_stocks_sheet_url: str,
    google_service_account_file: str,
    assembly_sheet_name: str,
    assembly_sheet_gid: int | None = None,
    row_factory,
) -> tuple[list, list[str]]:
    """Вернуть строки в порядке листа assembly (таблица bot_table / DEFAULT_STOCKS_SHEET_URL)."""
    warnings: list[str] = []
    if not list_rows:
        return list_rows, warnings

    sheet_url = str(default_stocks_sheet_url or "").strip()
    creds = str(google_service_account_file or "").strip()
    sheet = str(assembly_sheet_name or "assembly").strip() or "assembly"

    if not sheet_url:
        warnings.append(
            "DEFAULT_STOCKS_SHEET_URL не задан — порядок ТСД не применён (использован порядок по умолчанию)."
        )
        return list_rows, warnings
    if not creds:
        warnings.append(
            "GOOGLE_SERVICE_ACCOUNT_FILE не задан — порядок ТСД не применён."
        )
        return list_rows, warnings

    try:
        entries = load_assembly_entries_from_google_sheet(
            sheet_url,
            creds,
            sheet_name=sheet,
            sheet_gid=assembly_sheet_gid,
        )
    except WorksheetLookupError as exc:
        warnings.append(
            f"Порядок ТСД: {exc}. Использован порядок по умолчанию."
        )
        return list_rows, warnings
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Лист «{sheet}»: не удалось прочитать порядок ТСД ({exc}). "
            "Использован порядок по умолчанию."
        )
        return list_rows, warnings

    if not entries:
        warnings.append(f"Лист «{sheet}» пуст — порядок FBS по умолчанию.")
        return list_rows, warnings

    reordered = reorder_ozon_fbs_list_rows(list_rows, entries, row_factory=row_factory)
    assembly_keys = assembly_sku_keys(entries)

    fbs_skus_by_key: dict[str, str] = {}
    for row in list_rows:
        display = _norm_sku_raw(row.sku)
        if not display:
            continue
        key = sku_match_key(display)
        fbs_skus_by_key.setdefault(key, display)

    missing_keys = sorted(k for k in fbs_skus_by_key if k not in assembly_keys)
    if missing_keys:
        missing_labels = sorted((fbs_skus_by_key[k] for k in missing_keys), key=sku_match_key)
        sample = ", ".join(missing_labels[:8])
        tail = f" и ещё {len(missing_labels) - 8}" if len(missing_labels) > 8 else ""
        warnings.append(
            f"В листе «{sheet}» нет {len(missing_labels)} артикул(ов) из FBS ({sample}{tail}) — они в конце списка."
        )

    return reordered, warnings
