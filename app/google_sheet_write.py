"""Запись данных в Google Таблицу (новый лист) через service account."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from app.sheet_import import extract_sheet_id

_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

# Лист-шаблон: шапка + одна строка с форматированием и формулами (Картинка, название).
DEFAULT_FBS_TEMPLATE_SHEET = "FBSTemplate"
_FBS_DATA_COLS = 5


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
) -> str:
    """
    Копирует лист-шаблон (FBSTemplate), заполняет A, D, E: артикул, количество, номер отправления.
    Колонки B–C (картинка, название) не трогает — формулы из шаблона.
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
    postings = [[pn] for _, _, pn in rows]

    new_ws.update(f"A2:A{last_row}", skus, value_input_option="USER_ENTERED")
    new_ws.update(f"D2:D{last_row}", qtys, value_input_option="USER_ENTERED")
    new_ws.update(f"E2:E{last_row}", postings, value_input_option="USER_ENTERED")

    return f"https://docs.google.com/spreadsheets/d/{sh.id}/edit#gid={new_ws.id}"
