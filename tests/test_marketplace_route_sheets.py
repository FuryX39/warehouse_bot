"""Тесты маршрутных листов маркетплейсов."""

from __future__ import annotations

import io

from pypdf import PdfReader

from app.marketplace_route_sheets import (
    DEFAULT_ROUTE_STATUSES,
    DEFAULT_ROUTE_SUPPLIER,
    generate_vseinstrumenti_route_sheets_pdf,
    list_route_purchase_statuses,
    normalize_vseinstrumenti_route_sheet_payload,
    save_route_purchase_statuses,
    _STATUSES_FILE,
)


def test_vseinstrumenti_payload_defaults_and_date_format() -> None:
    payload = normalize_vseinstrumenti_route_sheet_payload(
        {"purchase_status": "ПСО", "delivery_date": "2026-06-19", "pallet_count": "3"}
    )
    assert payload.supplier == DEFAULT_ROUTE_SUPPLIER
    assert payload.delivery_date == "19.06.2026"
    assert payload.pallet_count == 3


def test_vseinstrumenti_pdf_pages_match_pallet_count() -> None:
    payload = normalize_vseinstrumenti_route_sheet_payload({"pallet_count": "3"})
    pdf = generate_vseinstrumenti_route_sheets_pdf(payload)
    assert pdf.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 3


def test_route_purchase_statuses_defaults_and_save(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.marketplace_route_sheets._STATUSES_FILE",
        tmp_path / "route_sheet_purchase_statuses.json",
    )
    defaults = list_route_purchase_statuses()
    assert [row["name"] for row in defaults] == list(DEFAULT_ROUTE_STATUSES)
    saved = save_route_purchase_statuses(
        [
            {"id": 1, "name": "КЗ", "is_default": True},
            {"id": 2, "name": "ПСО", "is_default": True},
            {"name": "Срочно"},
        ]
    )
    assert [row["name"] for row in saved] == ["КЗ", "ПСО", "Срочно"]
    assert _STATUSES_FILE.is_file()

