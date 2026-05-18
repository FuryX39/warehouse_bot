"""Запись данных в Google Таблицу (новый лист) через service account."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.sheet_import import extract_sheet_id

_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
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


def write_fbs_list_sheet(
    spreadsheet_url: str,
    credentials_path: str,
    sheet_title: str,
    header: list[str],
    rows: list[list],
) -> str:
    """
    Создаёт новый лист в таблице и записывает строки.
    Возвращает URL листа (с gid).
    """
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
    sh = gc.open_by_key(extract_sheet_id(spreadsheet_url))
    title = _unique_worksheet_title(sh, sheet_title)
    row_count = max(len(rows) + 1, 2)
    col_count = max(len(header), 1)
    worksheet = sh.add_worksheet(title=title, rows=row_count, cols=col_count)
    worksheet.update([header, *rows], value_input_option="USER_ENTERED")
    return f"https://docs.google.com/spreadsheets/d/{sh.id}/edit#gid={worksheet.id}"
