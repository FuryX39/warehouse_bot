"""Порядок строк FBS-списка по сборочному листу ТСД (лист assembly в Google Таблице)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.google_sheet_write import WorksheetLookupError, open_google_spreadsheet, resolve_worksheet


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
    """Индексы колонок артикула и ячейки (как в tables_examples/list.xlsx: A, C)."""
    sku_idx = 0
    place_idx = 2
    lowered = [str(c or "").strip().casefold() for c in header]
    for i, name in enumerate(lowered):
        if not name:
            continue
        if any(k in name for k in ("номенклат", "артикул", "sku", "offer")):
            sku_idx = i
        if "ячей" in name or name in {"место", "ячейка", "cell"}:
            place_idx = i
    return sku_idx, place_idx


def _looks_like_assembly_header(row: Sequence[str]) -> bool:
    if not row:
        return False
    joined = " ".join(str(c or "").strip().casefold() for c in row if str(c or "").strip())
    return any(k in joined for k in ("номенклат", "артикул", "ячей", "sku"))


def parse_assembly_sheet_values(values: list[list[str]]) -> list[AssemblySheetEntry]:
    """Разбор листа assembly: артикул + ячейка, порядок строк = маршрут ТСД."""
    if not values:
        return []
    start = 1 if _looks_like_assembly_header(values[0]) else 0
    sku_idx, place_idx = _detect_assembly_columns(values[0] if start else [])
    if not start:
        sku_idx, place_idx = 0, 2

    out: list[AssemblySheetEntry] = []
    for row in values[start:]:
        sku = _norm_cell(row, sku_idx)
        if not sku:
            continue
        place = _norm_cell(row, place_idx)
        out.append(AssemblySheetEntry(sku=sku, place=place, sort_index=len(out)))
    return out


def sku_sort_rank_from_assembly(entries: Sequence[AssemblySheetEntry]) -> dict[str, int]:
    """Первое появление артикула в листе assembly задаёт его позицию в маршруте."""
    rank: dict[str, int] = {}
    for entry in entries:
        key = entry.sku.casefold()
        if key not in rank:
            rank[key] = entry.sort_index
    return rank


def reorder_ozon_fbs_list_rows(
    list_rows: Sequence,
    sku_rank: dict[str, int],
    *,
    row_factory,
) -> list:
    """
    Сортировка по assembly: отправления целиком (для объединения ячеек в колонке E),
    внутри отправления — по маршруту ТСД.
    """
    if not list_rows:
        return []
    if not sku_rank:
        return [
            row_factory(i + 1, row.posting_number, row.sku, row.quantity, row.status)
            for i, row in enumerate(list_rows)
        ]

    big = 10**9
    by_posting: dict[str, list] = {}
    posting_first: dict[str, int] = {}
    for i, row in enumerate(list_rows):
        pn = str(row.posting_number)
        by_posting.setdefault(pn, []).append(row)
        if pn not in posting_first:
            posting_first[pn] = i

    def posting_sort_key(pn: str) -> tuple[int, int]:
        rows = by_posting[pn]
        ranks = [sku_rank.get(str(r.sku).casefold(), big) for r in rows]
        return (min(ranks), posting_first[pn])

    ordered_postings = sorted(by_posting.keys(), key=posting_sort_key)
    flat: list = []
    for pn in ordered_postings:
        rows = by_posting[pn]
        rows_sorted = sorted(
            enumerate(rows),
            key=lambda pair: (sku_rank.get(str(pair[1].sku).casefold(), big), pair[0]),
        )
        for _, row in rows_sorted:
            flat.append(row)

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
    fbs_list_sheet_url: str,
    google_service_account_file: str,
    assembly_sheet_name: str,
    assembly_sheet_gid: int | None = None,
    row_factory,
) -> tuple[list, list[str]]:
    """Вернуть строки в порядке листа assembly и предупреждения (если лист недоступен)."""
    warnings: list[str] = []
    if not list_rows:
        return list_rows, warnings

    sheet_url = str(fbs_list_sheet_url or "").strip()
    creds = str(google_service_account_file or "").strip()
    sheet = str(assembly_sheet_name or "assembly").strip() or "assembly"

    if not sheet_url or not creds:
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

    sku_rank = sku_sort_rank_from_assembly(entries)
    reordered = reorder_ozon_fbs_list_rows(list_rows, sku_rank, row_factory=row_factory)

    known = {str(r.sku).casefold() for r in list_rows}
    mapped = set(sku_rank.keys())
    missing = sorted(known - mapped)
    if missing:
        sample = ", ".join(missing[:8])
        tail = f" и ещё {len(missing) - 8}" if len(missing) > 8 else ""
        warnings.append(
            f"В листе «{sheet}» нет {len(missing)} артикул(ов) из FBS ({sample}{tail}) — они в конце списка."
        )

    return reordered, warnings
