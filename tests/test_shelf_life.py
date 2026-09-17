"""Срок годности: дата производства + годы, галочка в каталоге."""

from __future__ import annotations

from datetime import date

import pytest

from app.catalog_repository import CatalogRepository
from app.crm_repository import CrmRepository
from app.shelf_life import expiry_from_production, parse_production_date


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def test_expiry_from_production_adds_calendar_years() -> None:
    assert expiry_from_production("17.09.2026", years=3) == "17.09.2029"
    assert expiry_from_production("2026-09-17", years=3) == "17.09.2029"
    assert expiry_from_production("29.02.2024", years=1) == "28.02.2025"


def test_parse_production_date_rejects_garbage() -> None:
    assert parse_production_date("") is None
    with pytest.raises(ValueError, match="Некорректная дата"):
        parse_production_date("32.13.2026")


def test_catalog_shelf_life_requires_years_when_checked(db_url: str) -> None:
    repo = _repo(db_url)
    with pytest.raises(ValueError, match="срок годности"):
        repo.create_product(
            {
                "name": "CHEM",
                "sku": "CHEM-1",
                "code": "00201",
                "is_kit": False,
                "has_shelf_life": True,
                "components": [],
            }
        )
    product = repo.create_product(
        {
            "name": "CHEM",
            "sku": "CHEM-1",
            "code": "00201",
            "is_kit": False,
            "has_shelf_life": True,
            "shelf_life_years": 3,
            "components": [],
        }
    )
    assert product.has_shelf_life is True
    assert product.shelf_life_years == 3
    payload = repo.product_to_dict(product)
    assert payload["has_shelf_life"] is True
    assert payload["shelf_life_years"] == 3


def test_catalog_ignores_years_when_checkbox_off(db_url: str) -> None:
    repo = _repo(db_url)
    product = repo.create_product(
        {
            "name": "PLAIN",
            "sku": "PLAIN-1",
            "code": "00202",
            "is_kit": False,
            "has_shelf_life": False,
            "shelf_life_years": 5,
            "components": [],
        }
    )
    assert product.has_shelf_life is False
    assert product.shelf_life_years == 0
    assert repo.shelf_life_by_product_ids([product.id])[product.id] == (False, 0)


def test_add_calendar_years_keeps_date() -> None:
    from app.shelf_life import add_calendar_years

    assert add_calendar_years(date(2020, 2, 29), 4) == date(2024, 2, 29)
