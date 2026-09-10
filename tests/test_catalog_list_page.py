"""Пагинация списка каталога: 50 товаров на страницу."""

from __future__ import annotations

from app.catalog_repository import CATALOG_LIST_PAGE_SIZE, CatalogRepository
from app.crm_repository import CrmRepository


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def test_list_products_page_size_fifty(db_url: str) -> None:
    repo = _repo(db_url)
    for i in range(CATALOG_LIST_PAGE_SIZE + 5):
        repo.create_product(
            {
                "name": f"Item {i:03d}",
                "sku": f"SKU-{i:03d}",
                "code": f"{i:05d}",
                "is_kit": False,
                "barcodes": [],
                "components": [],
            }
        )
    total = repo.count_products({})
    assert total == CATALOG_LIST_PAGE_SIZE + 5
    page1 = repo.list_products({}, limit=CATALOG_LIST_PAGE_SIZE, offset=0)
    page2 = repo.list_products({}, limit=CATALOG_LIST_PAGE_SIZE, offset=CATALOG_LIST_PAGE_SIZE)
    assert len(page1) == CATALOG_LIST_PAGE_SIZE
    assert len(page2) == 5
    ids1 = {p.id for p in page1}
    ids2 = {p.id for p in page2}
    assert not ids1 & ids2
