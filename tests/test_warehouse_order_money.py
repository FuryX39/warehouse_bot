from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.warehouse_order_money import fetch_order_money, money_from_wb_order, money_from_yandex_order
from app.warehouse_users_repository import WarehouseUserRow
from app.web.warehouse_wms_routes import register_warehouse_wms_routes


def test_money_from_wb_order_uses_buyer_final_price() -> None:
    money = money_from_wb_order(
        {
            "salePrice": 56600,
            "price": 42500,
            "convertedPrice": 42500,
            "finalPrice": 41200,
            "convertedFinalPrice": 41200,
        }
    )
    assert money is not None
    assert money["buyer_paid"] == 412.0
    assert money["cabinet_price"] == 425.0
    assert money["listed_price"] == 566.0


def test_money_from_yandex_order_uses_items_total_not_deprecated_buyer_total() -> None:
    money = money_from_yandex_order(
        {
            "itemsTotal": 373.0,
            "buyerItemsTotal": 433.0,
            "buyerTotal": 433.0,
            "items": [{"price": 373.0, "buyerPrice": 373.0, "count": 1}],
        }
    )
    assert money is not None
    assert money["buyer_paid"] == 373.0
    assert money["cabinet_price"] == 373.0


def test_money_from_yandex_order_ignores_plus_points_in_buyer_total() -> None:
    money = money_from_yandex_order(
        {
            "itemsTotal": 1.0,
            "buyerItemsTotal": 6.0,
            "buyerTotal": 6.0,
            "items": [
                {
                    "price": 1.0,
                    "buyerPrice": 1.0,
                    "buyerPriceBeforeDiscount": 310.0,
                    "count": 1,
                }
            ],
            "subsidies": [
                {"type": "YANDEX_CASHBACK", "amount": 5.0},
                {"type": "SUBSIDY", "amount": 245.0},
            ],
        }
    )
    assert money is not None
    assert money["buyer_paid"] == 1.0
    assert money["cabinet_price"] == 1.0
    assert money["listed_price"] == 310.0


def test_hold_from_yandex_stats_sums_commissions() -> None:
    from app.warehouse_order_money import hold_from_yandex_stats

    assert hold_from_yandex_stats({"commissions": []}) is None
    assert (
        hold_from_yandex_stats(
            {
                "commissions": [
                    {"type": "FEE", "actual": 1.06},
                    {"type": "AGENCY", "actual": 0.12},
                    {"type": "PAYMENT_TRANSFER", "actual": 4.02},
                    {"type": "AUCTION_PROMOTION", "actual": 1.0},
                ]
            }
        )
        == 6.2
    )
    assert hold_from_yandex_stats({"commissions": [{"type": "AGENCY", "actual": 0.12}]}) is None


def test_attach_yandex_hold_prefers_stats_then_tariffs() -> None:
    from app.warehouse_order_money import attach_yandex_hold, money_from_yandex_order

    order = {
        "itemsTotal": 250.0,
        "items": [{"offerId": "SS771", "price": 250.0, "buyerPrice": 250.0, "count": 1}],
    }
    money = money_from_yandex_order(order)
    adapter = MagicMock()
    adapter.fetch_order_stats.return_value = {"commissions": [{"type": "FEE", "actual": 12.5}]}
    attached = attach_yandex_hold(adapter, "1", order, money)
    assert attached["mp_hold"] == 12.5
    assert attached["mp_hold_kind"] == "stats"

    adapter.fetch_order_stats.return_value = {"commissions": []}
    adapter.fetch_offer_snapshots.return_value = [{"offerId": "SS771", "marketCategoryId": 123}]
    adapter.calculate_tariffs.return_value = {
        "result": {"offers": [{"tariffs": [{"type": "FEE", "amount": 20.0}, {"type": "AGENCY", "amount": 3.0}]}]}
    }
    estimated = attach_yandex_hold(adapter, "1", order, money)
    assert estimated["mp_hold"] == 23.0
    assert estimated["mp_hold_kind"] == "tariffs_estimate"


def test_attach_wb_hold_uses_category_percent() -> None:
    from app.warehouse_order_money import attach_wb_hold, money_from_wb_order

    order = {"convertedFinalPrice": 36800, "convertedPrice": 39100, "nmId": 1, "supplierArticle": "SS925"}
    money = money_from_wb_order(order)
    adapter = MagicMock()
    adapter.fetch_product_card.return_value = {"subjectID": 42, "vendorCode": "SS925"}
    adapter.fetch_commission_tariffs.return_value = [{"subjectID": 42, "kgvpMarketplace": 20.0}]
    attached = attach_wb_hold(adapter, order, money)
    assert attached["mp_hold"] == 78.2
    assert attached["mp_hold_kind"] == "tariffs_estimate"


def test_money_from_cache_file(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "money.json"
    cache.write_text(
        '{"wildberries:1": {"source": "wildberries", "buyer_paid": 99.0}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ORDER_MONEY_CACHE", str(cache))
    coord = MagicMock()
    coord.adapters = []
    money = fetch_order_money(coord, "wildberries", "1")
    assert money is not None
    assert money["buyer_paid"] == 99.0


def test_fetch_order_money_skips_ozon_and_empty_adapters() -> None:
    coord = MagicMock()
    coord.adapters = []
    assert fetch_order_money(coord, "wildberries", "123") is None
    assert fetch_order_money(coord, "yandex_market", "615") is None
    assert fetch_order_money(coord, "ozon", "123-1") is None


def test_api_order_get_returns_order_without_live_money() -> None:
    order_row = MagicMock()
    order_row.source = "wildberries"
    order_row.posting_id = "5718747935"
    order_row.created_at_ts = 1
    orders_repo = MagicMock()
    orders_repo.get_order.return_value = order_row
    orders_repo.to_dict.return_value = {
        "id": 7,
        "number": "ЗК-00007",
        "status": "open",
        "source": "wildberries",
        "posting_id": "5718747935",
        "lines": [{"sku": "SS722", "name": "x", "quantity": 1}],
    }

    app = FastAPI()

    def require_warehouse_user() -> WarehouseUserRow:
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

    register_warehouse_wms_routes(
        app,
        orders_repo,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        require_warehouse_user,
        coordinator=MagicMock(),
    )
    resp = TestClient(app).get("/api/warehouse/orders/7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order"]["number"] == "ЗК-00007"
    assert "money" not in body
    orders_repo.set_order_money.assert_not_called()


def test_attach_yandex_hold_reads_nested_mapping_category() -> None:
    from app.warehouse_order_money import attach_yandex_hold, money_from_yandex_order

    order = {
        "itemsTotal": 250.0,
        "items": [{"offerId": "SS771", "price": 250.0, "buyerPrice": 250.0, "count": 1}],
    }
    money = money_from_yandex_order(order)
    adapter = MagicMock()
    adapter.fetch_order_stats.return_value = {"commissions": []}
    adapter.fetch_offer_snapshots.return_value = [
        {
            "offerId": "SS771",
            "offer": {"offerId": "SS771", "weightDimensions": {"length": 12, "width": 8, "height": 6, "weight": 0.4}},
            "mapping": {"marketCategoryId": 90490},
        }
    ]
    adapter.calculate_tariffs.return_value = {
        "result": {"offers": [{"tariffs": [{"type": "FEE", "amount": 40.0}]}]}
    }
    estimated = attach_yandex_hold(adapter, "1", order, money)
    assert estimated["mp_hold"] == 40.0
    offers = adapter.calculate_tariffs.call_args[0][0]
    assert offers[0]["categoryId"] == 90490
    assert offers[0]["length"] == 12


def test_money_from_stored_row() -> None:
    from types import SimpleNamespace

    from app.warehouse_order_money import money_from_stored

    assert money_from_stored(SimpleNamespace(buyer_paid=None, cabinet_price=None, mp_hold=None)) is None
    money = money_from_stored(
        SimpleNamespace(
            source="yandex_market",
            buyer_paid=1.0,
            cabinet_price=1.0,
            listed_price=310.0,
            mp_hold=6.2,
            mp_hold_kind="stats",
            mp_hold_note="факт",
        )
    )
    assert money is not None
    assert money["buyer_paid"] == 1.0
    assert money["mp_hold"] == 6.2
    assert money["mp_hold_kind"] == "stats"


def test_upsert_skips_header_money_for_yandex(db_url: str) -> None:
    from app.adapters.base import ReservationAction
    from app.catalog_repository import CatalogRepository
    from app.warehouse_order_sync import upsert_orders_from_actions
    from app.warehouse_orders_repository import WarehouseOrdersRepository

    CatalogRepository(db_url).init_schema()
    orders = WarehouseOrdersRepository(db_url)
    orders.init_schema()
    payload = {
        "source": "yandex_market",
        "buyer_paid": 250.0,
        "cabinet_price": 250.0,
        "listed_price": 400.0,
        "mp_hold": 37.5,
        "mp_hold_kind": "tariffs_estimate",
        "mp_hold_note": "примерный расчёт калькулятора тарифов",
        "currency": "RUB",
    }
    with patch("app.warehouse_order_sync.fetch_order_money", return_value=payload):
        upsert_orders_from_actions(
            orders,
            warehouse_id=1,
            actions=[
                ReservationAction(
                    source="yandex_market",
                    external_order_id="61460693504:SS771",
                    sku="SS771",
                    quantity=1,
                )
            ],
            coordinator=MagicMock(),
        )
    row = orders.get_by_posting("yandex_market", "61460693504")
    assert row is not None
    assert row.buyer_paid is None
    assert row.order_kind == "commission"


def test_api_order_get_uses_stored_money_when_fetch_empty() -> None:
    order_row = MagicMock()
    order_row.id = 3
    order_row.source = "yandex_market"
    order_row.posting_id = "59573472961"
    order_row.created_at_ts = 1
    order_row.buyer_paid = 1.0
    order_row.cabinet_price = 1.0
    order_row.listed_price = 310.0
    order_row.mp_hold = 6.2
    order_row.mp_hold_kind = "stats"
    order_row.mp_hold_note = "факт из отчёта stats/orders"
    orders_repo = MagicMock()
    orders_repo.get_order.return_value = order_row
    orders_repo.to_dict.return_value = {"id": 3, "number": "ЗК-00010", "source": "yandex_market"}

    app = FastAPI()

    def require_warehouse_user() -> WarehouseUserRow:
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

    register_warehouse_wms_routes(
        app,
        orders_repo,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        require_warehouse_user,
        coordinator=MagicMock(),
    )
    resp = TestClient(app).get("/api/warehouse/orders/3")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order"]["number"] == "ЗК-00010"
    assert "money" not in body
