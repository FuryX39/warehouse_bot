"""Удаление товара освобождает штрихкоды."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_repository import CatalogProductBarcode, CatalogRepository
from app.crm_repository import CrmRepository


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def test_delete_product_frees_barcode_for_reuse(db_url: str) -> None:
    repo = _repo(db_url)

    first = repo.create_product(
        {
            "name": "Old",
            "sku": "OLD-1",
            "code": "00001",
            "is_kit": False,
            "barcodes": [{"barcode": "ABC-12345", "label": "", "group": ""}],
            "components": [],
        }
    )
    assert first.id
    assert repo.delete_product(int(first.id)) is True

    with Session(repo.engine) as session:
        left = session.scalars(select(CatalogProductBarcode)).all()
        assert left == []

    second = repo.create_product(
        {
            "name": "New",
            "sku": "NEW-1",
            "code": "00002",
            "is_kit": False,
            "barcodes": [{"barcode": "ABC-12345", "label": "", "group": ""}],
            "components": [],
        }
    )
    assert any(b["barcode"] == "ABC-12345" for b in second.barcodes)


def test_init_schema_idempotent_and_barcode_reuse(db_url: str) -> None:
    repo = _repo(db_url)
    repo.init_schema()

    product = repo.create_product(
        {
            "name": "Reuse",
            "sku": "REUSE-1",
            "code": "00003",
            "is_kit": False,
            "barcodes": [{"barcode": "ORPHAN-99", "label": "", "group": ""}],
            "components": [],
        }
    )
    assert any(b["barcode"] == "ORPHAN-99" for b in product.barcodes)
    assert repo.delete_product(int(product.id)) is True

    again = repo.create_product(
        {
            "name": "Reuse2",
            "sku": "REUSE-2",
            "code": "00004",
            "is_kit": False,
            "barcodes": [{"barcode": "ORPHAN-99", "label": "", "group": ""}],
            "components": [],
        }
    )
    assert any(b["barcode"] == "ORPHAN-99" for b in again.barcodes)


def test_merge_product_barcode_appends_and_rejects_foreign(db_url: str) -> None:
    repo = _repo(db_url)
    first = repo.create_product(
        {
            "name": "One",
            "sku": "ONE-1",
            "code": "10001",
            "is_kit": False,
            "barcodes": [{"barcode": "OLD-111", "label": "", "group": ""}],
            "components": [],
        }
    )
    second = repo.create_product(
        {
            "name": "Two",
            "sku": "TWO-1",
            "code": "10002",
            "is_kit": False,
            "barcodes": [{"barcode": "OLD-222", "label": "", "group": ""}],
            "components": [],
        }
    )
    assert repo.merge_product_barcode(product_id=int(first.id), barcode="NEW-333") == "created"
    again = repo.get_product(int(first.id))
    codes = {item["barcode"] for item in again.barcodes}
    assert codes == {"OLD-111", "NEW-333"}
    assert repo.merge_product_barcode(
        product_id=int(first.id),
        barcode="NEW-444",
        label="ШК ВБ",
        group="Озон",
        touch_label=True,
        touch_group=True,
    ) == "created"
    labeled = repo.get_product(int(first.id))
    added = next(item for item in labeled.barcodes if item["barcode"] == "NEW-444")
    assert added["label"] == "ШК ВБ"
    assert added["group"] == "Озон"
    assert repo.merge_product_barcode(product_id=int(first.id), barcode="NEW-333") == "updated"
    try:
        repo.merge_product_barcode(product_id=int(second.id), barcode="NEW-333")
        raise AssertionError("expected duplicate barcode error")
    except ValueError as exc:
        assert "уже используется" in str(exc)
