"""Порядок FBS-списка по листу assembly из bot_table.

Правило намеренно простое:
- assembly!A:A = артикулы в нужном порядке;
- assembly!C:C = место (для информации/диагностики);
- assembly!E:E = количество (для информации/диагностики);
- FBS-строки сортируются по порядку артикулов из assembly сверху вниз.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.google_sheet_write import WorksheetLookupError, open_google_spreadsheet, resolve_worksheet

_INVISIBLE_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
_SKU_COL = 0
_PLACE_COL = 2
_QTY_COL = 4


def clean_sku(sku: object) -> str:
    """Артикул для вывода: убрать мусор вокруг значения, регистр не менять."""
    s = unicodedata.normalize("NFKC", str(sku or ""))
    s = s.replace("\u00a0", " ")
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, "")
    return s.strip().strip("'\"")


def sku_match_key(sku: object) -> str:
    """Ключ сравнения: регистр и случайные пробелы вокруг артикула не важны."""
    return clean_sku(sku).casefold()


@dataclass(frozen=True)
class AssemblySheetEntry:
    sku: str
    place: str
    quantity: int | None
    sort_index: int


def _cell(row: Sequence[object], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _parse_quantity(value: object) -> int | None:
    raw = clean_sku(value)
    if not raw:
        return None
    raw = raw.replace(",", ".")
    try:
        return int(float(raw))
    except ValueError:
        return None


def _looks_like_header(row: Sequence[object]) -> bool:
    a = _cell(row, _SKU_COL).casefold()
    c = _cell(row, _PLACE_COL).casefold()
    e = _cell(row, _QTY_COL).casefold()
    return (
        any(word in a for word in ("артикул", "номенклат", "sku", "offer"))
        or "ячей" in c
        or any(word in e for word in ("кол", "qty", "quantity"))
    )


def parse_assembly_sheet_values(values: list[list[object]]) -> list[AssemblySheetEntry]:
    """Прочитать assembly строго как A=артикул, C=место, E=количество."""
    if not values:
        return []

    out: list[AssemblySheetEntry] = []
    start = 1 if _looks_like_header(values[0]) else 0
    for row in values[start:]:
        sku = clean_sku(_cell(row, _SKU_COL))
        if not sku:
            continue
        out.append(
            AssemblySheetEntry(
                sku=sku,
                place=_cell(row, _PLACE_COL),
                quantity=_parse_quantity(_cell(row, _QTY_COL)),
                sort_index=len(out),
            )
        )
    return out


def assembly_sku_keys(entries: Sequence[AssemblySheetEntry]) -> set[str]:
    return {sku_match_key(entry.sku) for entry in entries if entry.sku}


def assembly_sku_rank(entries: Sequence[AssemblySheetEntry]) -> dict[str, int]:
    """Первое появление артикула в assembly!A:A задаёт его порядок в FBS."""
    rank: dict[str, int] = {}
    for entry in entries:
        key = sku_match_key(entry.sku)
        if key and key not in rank:
            rank[key] = entry.sort_index
    return rank


def reorder_ozon_fbs_list_rows(
    list_rows: Sequence,
    entries: Sequence[AssemblySheetEntry],
    *,
    row_factory,
) -> list:
    """
    Порядок FBS = порядок артикулов из assembly!A:A сверху вниз.
    Никаких дополнительных сортировок по отправлению, времени, алфавиту или месту.
    """
    if not list_rows:
        return []
    if not entries:
        return [
            row_factory(i + 1, row.posting_number, row.sku, row.quantity, row.status)
            for i, row in enumerate(list_rows)
        ]

    rank = assembly_sku_rank(entries)
    big = 10**9
    flat = sorted(
        enumerate(list_rows),
        key=lambda pair: (rank.get(sku_match_key(pair[1].sku), big), pair[0]),
    )
    return [
        row_factory(i + 1, row.posting_number, row.sku, row.quantity, row.status)
        for i, (_, row) in enumerate(flat)
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
        display = clean_sku(row.sku)
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
