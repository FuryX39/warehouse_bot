"""Цены, НДС, комиссия и ручное редактирование заказа покупателя."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.adapters.base import ReservationAction
from app.catalog_repository import CatalogProduct, CatalogRepository
from app.crm_repository import CrmRepository
from app.warehouse_order_pricing import split_vat
from app.warehouse_order_sync import upsert_orders_from_actions
from app.warehouse_orders_repository import (
    ORDER_KIND_COMMISSION,
    ORDER_KIND_SALE,
    SOURCE_MANUAL,
    WarehouseOrdersRepository,
)
from app.storage_warehouse_repository import StorageWarehouseRepository


def _stack(db_url: str):
    storage = StorageWarehouseRepository(db_url)
    storage.init_schema()
    catalog = CatalogRepository(db_url)
    catalog.init_schema()
    crm = CrmRepository(db_url)
    crm.init_schema()
    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()
    return {
        "storage": storage,
        "wh_id": int(storage.get_default_warehouse_id()),
        "catalog": catalog,
        "crm": crm,
        "orders": orders,
    }


def _add_product(catalog: CatalogRepository, sku: str) -> int:
    with Session(catalog.engine) as session:
        row = CatalogProduct(name=sku, sku=sku, code=sku[:8], created_at_ts=1, updated_at_ts=1)
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def _price_type_id(crm: CrmRepository, name: str) -> int:
    for item in crm.get_meta()["price_types"]:
        if str(item.get("name") or "").strip().casefold() == name.casefold():
            return int(item["id"])
    created, _ = crm.get_or_create_price_type_by_name(name)
    return int(created["id"])


def test_split_vat_22_percent() -> None:
    net, vat = split_vat(Decimal("374"), Decimal("22"))
    assert net == Decimal("306.56")
    assert vat == Decimal("67.44")


def test_apply_price_type_sets_zero_when_missing(db_url: str) -> None:
    stack = _stack(db_url)
    pid = _add_product(stack["catalog"], "SKU-A")
    retail_id = _price_type_id(stack["crm"], "Розничная цена")
    stack["catalog"].save_prices_for_price_type(retail_id, [{"product_id": pid, "price": "199.00"}])
    created = stack["orders"].create_manual_order(
        {
            "warehouse_id": stack["wh_id"],
            "counterparty_id": stack["crm"].list_counterparty_picker()[0]["id"]
            if stack["crm"].list_counterparty_picker()
            else _ensure_cp(stack["crm"]),
            "status": "open",
            "order_kind": ORDER_KIND_SALE,
            "vat_rate": "22",
            "comment": "ручной",
            "lines": [{"sku": "SKU-A", "product_id": pid, "name": "SKU-A", "quantity": 2}],
        }
    )
    missing_type = _price_type_id(stack["crm"], "Оптовая")
    updated = stack["orders"].apply_price_type(created.id, missing_type)
    assert updated is not None
    assert updated.lines[0].unit_price == Decimal("0.00")
    priced = stack["orders"].apply_price_type(created.id, retail_id)
    assert priced.lines[0].unit_price == Decimal("199.00")
    assert priced.lines[0].unit_price_net == Decimal("163.11")
    assert priced.lines[0].vat_amount == Decimal("35.89")


def _ensure_cp(crm: CrmRepository) -> int:
    from sqlalchemy.orm import Session as _S

    from app.crm_repository import CrmCounterparty

    with _S(crm.engine) as session:
        row = CrmCounterparty(full_name="Покупатель", created_at_ts=1, updated_at_ts=1)
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def test_manual_order_synthetic_posting_and_vat(db_url: str) -> None:
    stack = _stack(db_url)
    pid = _add_product(stack["catalog"], "SKU-A")
    cp = _ensure_cp(stack["crm"])
    row = stack["orders"].create_manual_order(
        {
            "warehouse_id": stack["wh_id"],
            "counterparty_id": cp,
            "lines": [
                {
                    "sku": "SKU-A",
                    "product_id": pid,
                    "name": "SKU-A",
                    "quantity": 1,
                    "unit_price": "374",
                }
            ],
        }
    )
    assert row.source == SOURCE_MANUAL
    assert row.posting_id == f"M-{row.number}"
    assert row.order_kind == ORDER_KIND_SALE
    assert row.lines_manual is True
    assert row.lines[0].unit_price == Decimal("374.00")
    assert row.lines[0].unit_price_net == Decimal("306.56")
    assert row.lines[0].cost == Decimal("0.00")
    payload = stack["orders"].to_dict(row)
    assert payload["posting_id"].startswith("M-ЗК-")


def test_sync_does_not_replace_manual_lines(db_url: str) -> None:
    stack = _stack(db_url)
    _add_product(stack["catalog"], "SKU-A")
    _add_product(stack["catalog"], "SKU-B")
    stack["orders"].upsert_from_posting(
        source="yandex_market",
        posting_id="61835966850",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SKU-A", "quantity": 1, "name": "A"}],
    )
    order = stack["orders"].get_by_posting("yandex_market", "61835966850")
    stack["orders"].update_order(
        order.id,
        {
            "lines": [
                {"sku": "SKU-B", "quantity": 4, "name": "B", "unit_price": "10"},
            ]
        },
    )
    locked = stack["orders"].get_order(order.id)
    assert locked.lines_manual is True
    assert [ln.sku for ln in locked.lines] == ["SKU-B"]
    upsert_orders_from_actions(
        stack["orders"],
        warehouse_id=stack["wh_id"],
        actions=[
            ReservationAction(
                source="yandex_market",
                external_order_id="61835966850:SKU-A",
                sku="SKU-A",
                quantity=9,
            )
        ],
    )
    after = stack["orders"].get_by_posting("yandex_market", "61835966850")
    assert [ln.sku for ln in after.lines] == ["SKU-B"]
    assert after.lines[0].quantity == 4
    assert after.lines[0].unit_price == Decimal("10.00")


def test_yandex_fill_empty_two_tariff_bases(db_url: str) -> None:
    stack = _stack(db_url)
    _add_product(stack["catalog"], "SS694")
    stack["orders"].upsert_from_posting(
        source="yandex_market",
        posting_id="61835966850",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SS694", "quantity": 1, "name": "item"}],
    )
    order = stack["orders"].get_by_posting("yandex_market", "61835966850")
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    adapter.fetch_order.return_value = {
        "items": [
            {
                "offerId": "SS694",
                "shopSku": "SS694",
                "count": 1,
                "buyerPrice": 374,
                "price": 1012,
                "subsidy": 638,
            }
        ]
    }
    adapter.fetch_offer_snapshots.return_value = [{"offerId": "SS694", "marketCategoryId": 90490}]

    def _calc(offers):
        price = offers[0]["price"]
        if price == 1012 or price == 1012.0:
            return {
                "result": {
                    "offers": [
                        {
                            "offerId": "SS694",
                            "tariffs": [
                                {"type": "FEE", "amount": 168.30},
                                {"type": "DELIVERY_TO_CUSTOMER", "amount": 18.70},
                                {"type": "MIDDLE_MILE", "amount": 100},
                                {"type": "AGENCY_COMMISSION", "amount": 9.99},
                            ],
                        }
                    ]
                }
            }
        return {
            "result": {
                "offers": [
                    {
                        "offerId": "SS694",
                        "tariffs": [
                            {"type": "AGENCY_COMMISSION", "amount": 0.12},
                            {"type": "PAYMENT_TRANSFER", "amount": 5.98},
                            {"type": "FEE", "amount": 50},
                        ]
                    }
                ]
            }
        }

    adapter.calculate_tariffs.side_effect = _calc
    coord = MagicMock()
    coord.adapters = [adapter]
    from app.warehouse_order_pricing import fill_empty_yandex_pricing

    filled = fill_empty_yandex_pricing(stack["orders"], coordinator=coord, order=order)
    assert filled.lines[0].unit_price == Decimal("374.00")
    assert filled.lines[0].buyer_price == Decimal("374.00")
    assert filled.lines[0].subsidy == Decimal("638.00")
    assert filled.lines[0].commission == Decimal("293.10")
    assert filled.comment == "SS694 - цена до соинвеста: 374+638"
    assert adapter.calculate_tariffs.call_count == 2

    stack["orders"].update_order(filled.id, {"comment": "ручной текст"})
    again = stack["orders"].get_order(filled.id)
    fill_empty_yandex_pricing(stack["orders"], coordinator=coord, order=again)
    kept = stack["orders"].get_order(filled.id)
    assert kept.comment == "ручной текст"
    assert kept.lines[0].unit_price == Decimal("374.00")


def test_yandex_does_not_overwrite_filled_price(db_url: str) -> None:
    stack = _stack(db_url)
    _add_product(stack["catalog"], "SS694")
    stack["orders"].upsert_from_posting(
        source="yandex_market",
        posting_id="1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "SS694", "quantity": 1, "name": "item"}],
    )
    order = stack["orders"].get_by_posting("yandex_market", "1")
    stack["orders"].update_order(
        order.id,
        {"lines": [{"sku": "SS694", "quantity": 1, "name": "item", "unit_price": "100", "commission": "1"}]},
    )
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    adapter.fetch_order.return_value = {
        "items": [{"offerId": "SS694", "buyerPrice": 374, "subsidy": 638, "count": 1}]
    }
    adapter.fetch_offer_snapshots.return_value = []
    coord = MagicMock()
    coord.adapters = [adapter]
    from app.warehouse_order_pricing import fill_empty_yandex_pricing

    filled = fill_empty_yandex_pricing(
        stack["orders"], coordinator=coord, order=stack["orders"].get_order(order.id)
    )
    assert filled.lines[0].unit_price == Decimal("100.00")
    assert filled.lines[0].commission == Decimal("1.00")
    adapter.calculate_tariffs.assert_not_called()


def test_comment_equal_posting_id_is_empty_for_yandex(db_url: str) -> None:
    from app.warehouse_order_pricing import comment_is_empty_for_fill

    assert comment_is_empty_for_fill("61835966850", "61835966850")
    assert comment_is_empty_for_fill("", "61835966850")
    assert not comment_is_empty_for_fill("свой текст", "61835966850")


def test_mp_order_kind_commission(db_url: str) -> None:
    stack = _stack(db_url)
    row = stack["orders"].upsert_from_posting(
        source="ozon",
        posting_id="POST-1",
        warehouse_id=stack["wh_id"],
        lines=[{"sku": "A", "quantity": 1, "name": "A"}],
    )
    assert row.order_kind == ORDER_KIND_COMMISSION
    assert row.comment == "POST-1"
