"""Короба в карточке товара: ШК и количество штук."""

from __future__ import annotations

import pytest

from app.catalog_repository import CatalogProductBox, CatalogRepository
from app.crm_repository import CrmRepository


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def _product(repo: CatalogRepository, *, sku: str, code: str, **extra):
    payload = {
        "name": sku,
        "sku": sku,
        "code": code,
        "is_kit": False,
        "barcodes": [],
        "components": [],
    }
    payload.update(extra)
    return repo.create_product(payload)


def test_create_and_update_product_boxes(db_url: str) -> None:
    repo = _repo(db_url)
    product = _product(
        repo,
        sku="BOX-SKU",
        code="00051",
        boxes=[{"barcode": "BOX-10", "quantity": 10}],
    )
    assert product.boxes == [{"barcode": "BOX-10", "quantity": 10}]
    payload = repo.product_to_dict(product)
    assert payload["boxes"] == [{"barcode": "BOX-10", "quantity": 10}]

    updated = repo.update_product(
        product.id,
        {
            "name": "BOX-SKU",
            "sku": "BOX-SKU",
            "code": "00051",
            "is_kit": False,
            "boxes": [
                {"barcode": "BOX-10", "quantity": 12},
                {"barcode": "BOX-24", "quantity": 24},
            ],
        },
    )
    assert updated is not None
    assert updated.boxes == [
        {"barcode": "BOX-10", "quantity": 12},
        {"barcode": "BOX-24", "quantity": 24},
    ]


def test_box_barcode_unique_and_not_unit_barcode(db_url: str) -> None:
    repo = _repo(db_url)
    _product(
        repo,
        sku="A",
        code="00061",
        barcodes=[{"barcode": "UNIT-1", "label": "", "group": ""}],
        boxes=[{"barcode": "BOX-A", "quantity": 6}],
    )
    with pytest.raises(ValueError, match="уже используется"):
        _product(
            repo,
            sku="B",
            code="00062",
            boxes=[{"barcode": "BOX-A", "quantity": 8}],
        )
    with pytest.raises(ValueError, match="штучный"):
        _product(
            repo,
            sku="C",
            code="00063",
            boxes=[{"barcode": "UNIT-1", "quantity": 8}],
        )


def test_same_product_rejects_unit_and_box_same_barcode(db_url: str) -> None:
    repo = _repo(db_url)
    with pytest.raises(ValueError, match="одновременно"):
        _product(
            repo,
            sku="D",
            code="00071",
            barcodes=[{"barcode": "SAME-1", "label": "", "group": ""}],
            boxes=[{"barcode": "SAME-1", "quantity": 10}],
        )


def test_find_product_by_box_barcode(db_url: str) -> None:
    repo = _repo(db_url)
    product = _product(
        repo,
        sku="E",
        code="00081",
        boxes=[{"barcode": "BOX-FIND", "quantity": 4}],
    )
    found = repo.find_product_by_barcode("BOX-FIND")
    assert found is not None
    assert found.id == product.id


def test_delete_product_removes_boxes(db_url: str) -> None:
    repo = _repo(db_url)
    product = _product(
        repo,
        sku="F",
        code="00091",
        boxes=[{"barcode": "BOX-DEL", "quantity": 3}],
    )
    assert repo.delete_product(int(product.id)) is True
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    with Session(repo.engine) as session:
        left = session.scalars(select(CatalogProductBox)).all()
        assert left == []
    reused = _product(
        repo,
        sku="G",
        code="00092",
        boxes=[{"barcode": "BOX-DEL", "quantity": 5}],
    )
    assert reused.boxes[0]["quantity"] == 5


def test_product_box_quantities_by_id(db_url: str) -> None:
    repo = _repo(db_url)
    product = _product(
        repo,
        sku="QTY",
        code="00111",
        boxes=[
            {"barcode": "BOX-Q-10", "quantity": 10},
            {"barcode": "BOX-Q-24", "quantity": 24},
        ],
    )
    empty = _product(repo, sku="NOBOX", code="00112")
    found = repo.product_box_quantities_by_id([product.id, empty.id, 0])
    assert found[int(product.id)] == {10, 24}
    assert found[int(empty.id)] == set()
    assert repo.product_box_quantities_by_id([]) == {}


def test_box_quantity_must_be_positive(db_url: str) -> None:
    repo = _repo(db_url)
    with pytest.raises(ValueError, match="не меньше 1"):
        _product(
            repo,
            sku="H",
            code="00101",
            boxes=[{"barcode": "BOX-ZERO", "quantity": 0}],
        )
