"""Запись данных в Google Таблицу (новый лист) через service account."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import unicodedata
from typing import Sequence

from app.sheet_import import extract_sheet_id

_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

# Лист-шаблон: шапка + одна строка с форматированием и формулами (Картинка, название).
DEFAULT_FBS_TEMPLATE_SHEET = "FBSTemplate"
_FBS_DATA_COLS = 5
_FBS_POSTING_COL_INDEX = 4
_FBS_POSTING_NORMAL_FONT_PT = 12
_FBS_POSTING_HIGHLIGHT_FONT_PT = 14
_UPDATE_CELLS_FIELDS = (
    "userEnteredValue,"
    "textFormatRuns.startIndex,"
    "textFormatRuns.format.bold,"
    "textFormatRuns.format.fontSize"
)


def ozon_posting_highlight_range(posting_number: str) -> tuple[int, int]:
    """
    Диапазон [start, end) для подсветки как на этикетке Ozon:
    4 символа непосредственно перед первым «-» (если дефиса нет — первые 4 символа).
    """
    pn = str(posting_number or "").strip()
    if not pn:
        return 0, 0
    dash = pn.find("-")
    if dash <= 0:
        return 0, min(4, len(pn))
    start = max(0, dash - 4)
    return start, dash


def yandex_order_highlight_range(order_id: str) -> tuple[int, int]:
    """
    Диапазон [start, end) для подсветки заказа Яндекса:
    последние 4 цифры номера (если цифр меньше 4 — все найденные цифры).
    """
    oid = str(order_id or "").strip()
    if not oid:
        return 0, 0
    digit_matches = list(re.finditer(r"\d", oid))
    if not digit_matches:
        return max(0, len(oid) - 4), len(oid)
    selected = digit_matches[-4:]
    return selected[0].start(), selected[-1].end()


def _posting_cell_with_highlight(
    posting_number: str,
    *,
    normal_pt: int = _FBS_POSTING_NORMAL_FONT_PT,
    highlight_pt: int = _FBS_POSTING_HIGHLIGHT_FONT_PT,
    highlight_style: str = "ozon",
) -> dict:
    """Ячейка с rich text: код сборки жирным и крупнее."""
    pn = str(posting_number).strip()
    if highlight_style == "yandex_last4_digits":
        hi_start, hi_end = yandex_order_highlight_range(pn)
    else:
        hi_start, hi_end = ozon_posting_highlight_range(pn)
    normal_fmt = {"bold": False, "fontSize": normal_pt}
    hi_fmt = {"bold": True, "fontSize": highlight_pt}
    runs: list[dict] = []
    if hi_start > 0:
        runs.append({"format": normal_fmt, "startIndex": 0})
    if hi_end > hi_start:
        runs.append({"format": hi_fmt, "startIndex": hi_start})
    if hi_end < len(pn):
        runs.append({"format": normal_fmt, "startIndex": hi_end})
    if not runs:
        runs.append({"format": normal_fmt, "startIndex": 0})
    return {
        "userEnteredValue": {"stringValue": pn},
        "textFormatRuns": runs,
    }


def _merge_consecutive_posting_cells(
    worksheet,
    posting_numbers: Sequence[str],
    *,
    column_index: int = _FBS_POSTING_COL_INDEX,
) -> None:
    """Вертикально объединяет ячейки колонки отправления с одинаковым номером подряд."""
    if len(posting_numbers) < 2:
        return
    requests: list[dict] = []
    i = 0
    while i < len(posting_numbers):
        pn = posting_numbers[i]
        j = i + 1
        while j < len(posting_numbers) and posting_numbers[j] == pn:
            j += 1
        if j - i > 1:
            start_row = 1 + i
            end_row = 1 + j
            cell_range = {
                "sheetId": worksheet.id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            }
            requests.append({"mergeCells": {"range": cell_range, "mergeType": "MERGE_ALL"}})
            requests.append(
                {
                    "repeatCell": {
                        "range": cell_range,
                        "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE"}},
                        "fields": "userEnteredFormat.verticalAlignment",
                    }
                }
            )
        i = j
    if requests:
        worksheet.spreadsheet.batch_update({"requests": requests})


def _fill_posting_column_highlighted(
    worksheet,
    posting_numbers: Sequence[str],
    *,
    highlight_style: str = "ozon",
) -> None:
    if not posting_numbers:
        return
    rows = [
        {"values": [_posting_cell_with_highlight(pn, highlight_style=highlight_style)]}
        for pn in posting_numbers
    ]
    worksheet.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "updateCells": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": 1,
                            "endRowIndex": 1 + len(posting_numbers),
                            "startColumnIndex": _FBS_POSTING_COL_INDEX,
                            "endColumnIndex": _FBS_POSTING_COL_INDEX + 1,
                        },
                        "rows": rows,
                        "fields": _UPDATE_CELLS_FIELDS,
                    }
                }
            ]
        }
    )


def fbs_list_sheet_title(when: datetime | None = None) -> str:
    """Имя листа: «FBS список» + дата (DD.MM.YYYY)."""
    dt = when or datetime.now()
    return f"FBS список{dt.strftime('%d.%m.%Y')}"


def _unique_worksheet_title(sh, base: str) -> str:
    existing = {ws.title for ws in sh.worksheets()}
    if base not in existing:
        return base
    suffix = datetime.now().strftime("%H%M")
    candidate = f"{base} {suffix}"
    n = 2
    while candidate in existing:
        candidate = f"{base} {suffix}_{n}"
        n += 1
    return candidate


def _open_spreadsheet(spreadsheet_url: str, credentials_path: str):
    cred_path = Path(credentials_path).expanduser()
    if not cred_path.is_file():
        raise FileNotFoundError(f"Файл service account не найден: {cred_path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Установите gspread и google-auth: pip install gspread google-auth"
        ) from exc

    creds = Credentials.from_service_account_file(str(cred_path), scopes=_SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(extract_sheet_id(spreadsheet_url))


def open_google_spreadsheet(spreadsheet_url: str, credentials_path: str):
    """Открыть таблицу по URL (для чтения листа assembly и записи FBS-списков)."""
    return _open_spreadsheet(spreadsheet_url, credentials_path)


class WorksheetLookupError(LookupError):
    """Лист не найден; available_titles — имена вкладок в таблице (для подсказки в UI)."""

    def __init__(self, message: str, *, available_titles: list[str] | None = None):
        super().__init__(message)
        self.available_titles = list(available_titles or [])


def parse_worksheet_gid(raw: str) -> int | None:
    """Числовой gid вкладки из FBS_ASSEMBLY_SHEET_GID, URL (#gid=...) или gid:149721613."""
    s = str(raw or "").strip()
    if not s:
        return None
    if "#gid=" in s:
        s = s.rsplit("#gid=", maxsplit=1)[-1].split("&", 1)[0].strip()
    if s.casefold().startswith("gid:"):
        s = s[4:].strip()
    try:
        return int(s)
    except ValueError:
        return None


def _norm_sheet_title(title: str) -> str:
    return unicodedata.normalize("NFKC", str(title or "").strip()).casefold()


def resolve_worksheet(
    spreadsheet,
    *,
    sheet_name: str = "",
    sheet_gid: int | None = None,
):
    """
    Найти вкладку по gid (из URL #gid=...) и/или имени.
    Имя ищется без учёта регистра; в sheet_name допустимо gid:12345.
    """
    import gspread

    name = str(sheet_name or "").strip()
    gid = sheet_gid
    if gid is None:
        parsed = parse_worksheet_gid(name)
        if parsed is not None:
            gid = parsed
            name = ""

    if gid is not None:
        ws = spreadsheet.get_worksheet_by_id(int(gid))
        if ws is not None:
            return ws

    if name:
        try:
            return spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            pass
        name_norm = _norm_sheet_title(name)
        for ws in spreadsheet.worksheets():
            if _norm_sheet_title(ws.title) == name_norm:
                return ws

    titles = [ws.title for ws in spreadsheet.worksheets()]
    if gid is not None and name:
        msg = f"Лист «{name}» (gid {gid}) не найден"
    elif gid is not None:
        msg = f"Лист с gid {gid} не найден"
    elif name:
        msg = f"Лист «{name}» не найден"
    else:
        msg = "Не указано имя листа"
    if titles:
        shown = ", ".join(titles[:20])
        if len(titles) > 20:
            shown += f" … (+{len(titles) - 20})"
        msg += f". Доступные листы: {shown}"
    raise WorksheetLookupError(msg, available_titles=titles)


def describe_fbs_google_sheets(
    *,
    default_stocks_sheet_url: str,
    fbs_list_sheet_url: str,
    google_service_account_file: str,
    assembly_sheet_name: str,
    assembly_sheet_gid: int | None,
    fbs_list_template_sheet: str,
) -> dict:
    """Ссылки на bot_table (assembly) и FBS-таблицу (запись списков) для вкладки FBS."""
    bot_url = str(default_stocks_sheet_url or "").strip()
    fbs_url = str(fbs_list_sheet_url or "").strip()
    creds = str(google_service_account_file or "").strip()
    assembly_name = str(assembly_sheet_name or "assembly").strip() or "assembly"
    template_name = str(fbs_list_template_sheet or "FBSTemplate").strip() or "FBSTemplate"

    out: dict = {
        "sheet_configured": bool(creds and bot_url and fbs_url),
        "default_stocks_sheet_url": None,
        "fbs_list_sheet_url": None,
        "assembly_sheet_name": assembly_name,
        "assembly_sheet_gid": assembly_sheet_gid,
        "template_sheet_name": template_name,
        "assembly_sheet_url": None,
        "assembly_ok": False,
        "assembly_message": "",
        "worksheet_titles": [],
    }

    if bot_url:
        try:
            bot_id = extract_sheet_id(bot_url)
            out["default_stocks_sheet_url"] = f"https://docs.google.com/spreadsheets/d/{bot_id}/edit"
        except ValueError as exc:
            out["assembly_message"] = f"Некорректный DEFAULT_STOCKS_SHEET_URL: {exc}"
            return out
    else:
        out["assembly_message"] = (
            "В .env не задан DEFAULT_STOCKS_SHEET_URL (таблица bot_table) — оттуда читается лист assembly."
        )

    if fbs_url:
        try:
            fbs_id = extract_sheet_id(fbs_url)
            out["fbs_list_sheet_url"] = f"https://docs.google.com/spreadsheets/d/{fbs_id}/edit"
        except ValueError:
            out["fbs_list_sheet_url"] = None

    if not creds:
        if not out["assembly_message"]:
            out["assembly_message"] = "В .env не задан GOOGLE_SERVICE_ACCOUNT_FILE."
        return out
    if not bot_url:
        return out

    base = out["default_stocks_sheet_url"]
    gid = assembly_sheet_gid
    if gid is not None:
        out["assembly_sheet_gid"] = gid
        out["assembly_sheet_url"] = f"{base}#gid={gid}"

    try:
        sh = _open_spreadsheet(bot_url, creds)
        titles = [ws.title for ws in sh.worksheets()]
        out["worksheet_titles"] = titles
        ws = resolve_worksheet(sh, sheet_name=assembly_name, sheet_gid=gid)
        out["assembly_sheet_gid"] = ws.id
        out["assembly_sheet_url"] = f"{base}#gid={ws.id}"
        out["assembly_sheet_name"] = ws.title
        out["assembly_ok"] = True
        out["assembly_message"] = (
            f"Лист «{ws.title}» в bot_table найден и доступен для чтения."
        )
    except WorksheetLookupError as exc:
        out["assembly_message"] = str(exc)
        out["worksheet_titles"] = exc.available_titles or out["worksheet_titles"]
    except FileNotFoundError as exc:
        out["assembly_message"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        out["assembly_message"] = f"Ошибка доступа к bot_table: {exc}"

    return out


def _extend_template_data_rows(worksheet, *, data_row_count: int) -> None:
    """Копирует строку 2 шаблона вниз, сохраняя формат и формулы."""
    if data_row_count <= 1:
        return
    sheet_id = worksheet.id
    worksheet.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 2,
                            "startColumnIndex": 0,
                            "endColumnIndex": _FBS_DATA_COLS,
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "endRowIndex": 1 + data_row_count,
                            "startColumnIndex": 0,
                            "endColumnIndex": _FBS_DATA_COLS,
                        },
                        "pasteType": "PASTE_NORMAL",
                    }
                }
            ]
        }
    )


def write_fbs_list_from_template(
    spreadsheet_url: str,
    credentials_path: str,
    sheet_title: str,
    rows: Sequence[tuple[str, int, str]],
    *,
    template_sheet_name: str = DEFAULT_FBS_TEMPLATE_SHEET,
    highlight_style: str = "ozon",
) -> str:
    """
    Копирует лист-шаблон (FBSTemplate), заполняет A, D, E: артикул, количество, номер отправления.
    Колонки B–C (картинка, название) не трогает — формулы из шаблона.
    Подряд идущие строки с одним номером отправления (колонка E) объединяются по вертикали.
    """
    if not rows:
        raise ValueError("Нет строк для выгрузки в Google Таблицу")

    sh = _open_spreadsheet(spreadsheet_url, credentials_path)
    try:
        template_ws = sh.worksheet(template_sheet_name)
    except Exception as exc:
        raise RuntimeError(
            f"Лист «{template_sheet_name}» не найден в таблице. "
            "Создайте шаблон с шапкой и одной строкой данных."
        ) from exc

    title = _unique_worksheet_title(sh, sheet_title)
    new_ws = template_ws.duplicate(new_sheet_name=title)
    _extend_template_data_rows(new_ws, data_row_count=len(rows))

    last_row = 1 + len(rows)
    skus = [[sku] for sku, _, _ in rows]
    qtys = [[str(qty)] for _, qty, _ in rows]
    posting_numbers = [pn for _, _, pn in rows]

    new_ws.update(f"A2:A{last_row}", skus, value_input_option="USER_ENTERED")
    new_ws.update(f"D2:D{last_row}", qtys, value_input_option="USER_ENTERED")
    _fill_posting_column_highlighted(new_ws, posting_numbers, highlight_style=highlight_style)
    _merge_consecutive_posting_cells(new_ws, posting_numbers)

    return f"https://docs.google.com/spreadsheets/d/{sh.id}/edit#gid={new_ws.id}"
