"""Тесты поиска вкладки Google Таблицы по имени и gid."""

from __future__ import annotations

from types import SimpleNamespace

from app.google_sheet_write import WorksheetLookupError, parse_worksheet_gid, resolve_worksheet

try:
    from gspread.exceptions import WorksheetNotFound
except ImportError:  # pragma: no cover
    class WorksheetNotFound(Exception):
        pass


class _FakeSpreadsheet:
    def __init__(self, worksheets: list[SimpleNamespace]):
        self._worksheets = worksheets

    def get_worksheet_by_id(self, gid: int):
        for ws in self._worksheets:
            if ws.id == gid:
                return ws
        return None

    def worksheet(self, title: str):
        for ws in self._worksheets:
            if ws.title == title:
                return ws
        raise WorksheetNotFound(title)

    def worksheets(self):
        return list(self._worksheets)


def test_parse_worksheet_gid() -> None:
    assert parse_worksheet_gid("149721613") == 149721613
    assert parse_worksheet_gid("gid:149721613") == 149721613
    assert parse_worksheet_gid("GID:42") == 42
    assert parse_worksheet_gid("") is None
    assert parse_worksheet_gid("assembly") is None


def test_resolve_worksheet_by_gid() -> None:
    sh = _FakeSpreadsheet([SimpleNamespace(id=149721613, title="Сборка ТСД")])
    ws = resolve_worksheet(sh, sheet_gid=149721613)
    assert ws.title == "Сборка ТСД"


def test_resolve_worksheet_case_insensitive_name() -> None:
    sh = _FakeSpreadsheet([SimpleNamespace(id=1, title="Assembly")])
    ws = resolve_worksheet(sh, sheet_name="assembly")
    assert ws.title == "Assembly"


def test_resolve_worksheet_gid_prefix_in_name() -> None:
    sh = _FakeSpreadsheet([SimpleNamespace(id=99, title="route")])
    ws = resolve_worksheet(sh, sheet_name="gid:99")
    assert ws.id == 99


def test_resolve_worksheet_not_found_lists_titles() -> None:
    sh = _FakeSpreadsheet(
        [
            SimpleNamespace(id=1, title="FBSTemplate"),
            SimpleNamespace(id=2, title="Сборка"),
        ]
    )
    try:
        resolve_worksheet(sh, sheet_name="assembly")
        raise AssertionError("expected WorksheetLookupError")
    except WorksheetLookupError as err:
        assert "FBSTemplate" in str(err)
        assert err.available_titles == ["FBSTemplate", "Сборка"]
