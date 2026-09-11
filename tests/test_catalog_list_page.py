"""Пагинация списка каталога: 50 товаров на страницу."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.catalog_repository import CATALOG_LIST_PAGE_SIZE, CatalogRepository
from app.crm_repository import CrmRepository
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_catalog_routes import register_warehouse_catalog_routes


def _repo(db_url: str) -> CatalogRepository:
    crm = CrmRepository(db_url)
    crm.init_schema()
    repo = CatalogRepository(db_url)
    repo.init_schema()
    return repo


def _user() -> WarehouseUserRow:
    return WarehouseUserRow(
        id=1,
        login="admin",
        display_name="Admin",
        group_id=None,
        group_name="",
        telegram_nick="",
        is_admin=True,
        is_active=True,
        permissions={},
        created_at_ts=0,
        updated_at_ts=0,
    )


def _seed_catalog(repo: CatalogRepository, count: int) -> None:
    for i in range(count):
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


def _catalog_client(repo: CatalogRepository) -> TestClient:
    app = FastAPI()

    def require_warehouse_user() -> WarehouseUserRow:
        return _user()

    register_warehouse_catalog_routes(app, repo, require_warehouse_user)
    return TestClient(app)


def test_list_products_page_size_fifty(db_url: str) -> None:
    repo = _repo(db_url)
    _seed_catalog(repo, CATALOG_LIST_PAGE_SIZE + 5)
    total = repo.count_products({})
    assert total == CATALOG_LIST_PAGE_SIZE + 5
    page1 = repo.list_products({}, limit=CATALOG_LIST_PAGE_SIZE, offset=0)
    page2 = repo.list_products({}, limit=CATALOG_LIST_PAGE_SIZE, offset=CATALOG_LIST_PAGE_SIZE)
    assert len(page1) == CATALOG_LIST_PAGE_SIZE
    assert len(page2) == 5
    ids1 = {p.id for p in page1}
    ids2 = {p.id for p in page2}
    assert not ids1 & ids2


def test_catalog_list_without_page_returns_all_products(db_url: str) -> None:
    extra = 5
    last_sku = f"SKU-{CATALOG_LIST_PAGE_SIZE + extra - 1:03d}"
    repo = _repo(db_url)
    _seed_catalog(repo, CATALOG_LIST_PAGE_SIZE + extra)
    client = _catalog_client(repo)

    paged = client.get("/api/warehouse/catalog/products", params={"page": 1})
    assert paged.status_code == 200, paged.text
    body = paged.json()
    assert body["total"] == CATALOG_LIST_PAGE_SIZE + extra
    assert len(body["products"]) == CATALOG_LIST_PAGE_SIZE
    assert body["limit"] == CATALOG_LIST_PAGE_SIZE
    assert last_sku not in {p["sku"] for p in body["products"]}

    full = client.get("/api/warehouse/catalog/products")
    assert full.status_code == 200, full.text
    all_body = full.json()
    skus = {p["sku"] for p in all_body["products"]}
    assert all_body["total"] == CATALOG_LIST_PAGE_SIZE + extra
    assert len(all_body["products"]) == CATALOG_LIST_PAGE_SIZE + extra
    assert last_sku in skus

    found = client.get("/api/warehouse/catalog/products", params={"q": last_sku})
    assert found.status_code == 200, found.text
    found_skus = {p["sku"] for p in found.json()["products"]}
    assert found_skus == {last_sku}
