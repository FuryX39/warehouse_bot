"""ТН ВЭД и ссылки на площадки в карточке товара."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogProductLink, CatalogRepository
from app.crm_repository import CrmRepository


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def test_product_tnved_and_links(db_url: str) -> None:
    repo = _repo(db_url)
    product = repo.create_product(
        {
            "name": "NanoGlass",
            "sku": "SS100",
            "code": "00100",
            "tnved": "3402 500000",
            "links": [
                {
                    "counterparty": "Wildberries",
                    "counterparty_sku": "SS100",
                    "url": "https://www.wildberries.ru/catalog/1/detail.aspx",
                },
                {"counterparty": "", "counterparty_sku": "", "url": ""},
                {"counterparty": "Ozon", "counterparty_sku": "", "url": ""},
            ],
        }
    )
    assert product.tnved == "3402500000"
    assert product.links == [
        {
            "counterparty": "Wildberries",
            "counterparty_sku": "SS100",
            "url": "https://www.wildberries.ru/catalog/1/detail.aspx",
        },
        {"counterparty": "Ozon", "counterparty_sku": "", "url": ""},
    ]
    payload = repo.product_to_dict(product)
    assert payload["tnved"] == "3402500000"
    assert payload["links"] == product.links

    updated = repo.update_product(
        product.id,
        {
            "name": "NanoGlass",
            "sku": "SS100",
            "code": "00100",
            "tnved": "",
            "links": [{"counterparty": "", "counterparty_sku": "OZ-1", "url": "https://ozon.ru/p/1"}],
        },
    )
    assert updated is not None
    assert updated.tnved == ""
    assert updated.links == [
        {"counterparty": "", "counterparty_sku": "OZ-1", "url": "https://ozon.ru/p/1"}
    ]

    kept = repo.update_product(
        product.id,
        {"name": "NanoGlass", "sku": "SS100", "code": "00100"},
    )
    assert kept is not None
    assert kept.links == updated.links

    assert repo.delete_product(product.id) is True
    with Session(repo.engine) as session:
        left = session.scalars(select(CatalogProductLink)).all()
    assert left == []


def test_tnved_too_long(db_url: str) -> None:
    repo = _repo(db_url)
    with pytest.raises(ValueError, match="ТН ВЭД"):
        repo.create_product(
            {"name": "X", "sku": "X1", "code": "00001", "tnved": "1" * 33}
        )
