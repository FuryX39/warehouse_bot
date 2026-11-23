"""Бидер WB: агрегация Promotion API и HTTP-маршруты."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.warehouse_permissions import full_access_permissions, normalize_permissions
from app.warehouse_users_repository import WarehouseUserRow
from app.wb_promotion import (
    WbPromotionClient,
    get_campaign,
    list_campaigns,
    serialize_campaign,
)
from app.web.warehouse_bidder_routes import register_warehouse_bidder_routes


def _user() -> WarehouseUserRow:
    return WarehouseUserRow(
        id=1,
        login="admin",
        display_name="admin",
        group_id=None,
        group_name="",
        telegram_nick="",
        is_admin=True,
        is_active=True,
        permissions={},
        created_at_ts=0,
        updated_at_ts=0,
    )


class _FakeResp:
    def __init__(self, status_code: int, payload, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = {}
        self.reason = "OK"
        self.url = "https://advert-api.wildberries.ru/x"
        self.text = text or ("" if payload is None else "ok")

    def json(self):
        return self._payload


def _advert() -> dict:
    return {
        "advertId": 42038717,
        "status": 9,
        "settings": {
            "name": "AbrasivePrep+Glass Applicator SS579/SS590",
            "paymentType": "cpc",
            "placements": {"search": True, "recommendations": False},
            "nms": [{"nmId": 1399959999, "name": "SS579"}],
        },
        "timestamps": {
            "created": "2026-11-19T10:00:00+03:00",
            "started": "2026-11-19T10:05:00+03:00",
            "updated": "2026-11-23T09:00:00+03:00",
        },
    }


def _stats() -> dict:
    return {
        "advertId": 42038717,
        "views": 16731,
        "clicks": 780,
        "orders": 53,
        "sum": 12695.24,
        "cpc": 16.28,
        "ctr": 4.66,
        "cr": 6.79,
        "atbs": 152,
        "shks": 53,
        "sum_price": 47234,
        "canceled": 1,
        "days": [
            {
                "date": "2026-11-22T00:00:00Z",
                "views": 4177,
                "clicks": 178,
                "orders": 16,
                "sum": 3002.36,
                "ctr": 4.26,
                "sum_price": 14000,
                "apps": [
                    {
                        "nms": [
                            {
                                "nmId": 1399959999,
                                "name": "SS579",
                                "views": 4177,
                                "clicks": 178,
                                "orders": 16,
                                "sum": 3002.36,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_serialize_campaign_main_fields() -> None:
    row = serialize_campaign(
        _advert(),
        budget={"advertId": 42038717, "total": 2317, "currency": "RUB"},
        stats=_stats(),
        include_details=True,
    )
    assert row["advert_id"] == 42038717
    assert row["name"].startswith("AbrasivePrep")
    assert row["status_label"] == "Идут показы"
    assert row["payment_type"] == "cpc"
    assert row["budget"] == 2317
    assert row["stats"]["orders"] == 53
    assert row["stats"]["drr"] == 26.88
    assert row["days"][0]["date"] == "2026-11-22"
    assert row["nm_stats"][0]["nm_id"] == 1399959999
    assert row["nm_stats"][0]["orders"] == 16


def test_list_campaigns_merges_budget_and_stats() -> None:
    def request_fn(method, url, **kwargs):
        if url.endswith("/api/advert/v3/adverts"):
            return _FakeResp(200, {"adverts": [_advert()], "next": ""})
        if url.endswith("/api/advert/v2/budget"):
            return _FakeResp(200, {"adverts": [{"advertId": 42038717, "total": 2317, "currency": "RUB"}]})
        if url.endswith("/adv/v3/fullstats"):
            return _FakeResp(200, [_stats()])
        return _FakeResp(404, {"detail": url}, text="missing")

    client = WbPromotionClient("tok", request_fn=request_fn, sleep_fn=lambda _s: None)
    payload = list_campaigns(client, status="active", days=7, today=date(2026, 11, 23))
    assert payload["begin"] == "2026-11-17"
    assert payload["end"] == "2026-11-23"
    assert payload["count"] == 1
    campaign = payload["campaigns"][0]
    assert campaign["advert_id"] == 42038717
    assert campaign["budget"] == 2317
    assert campaign["stats"]["spend"] == 12695.24
    assert payload["totals"]["orders"] == 53


def test_get_campaign_not_found() -> None:
    def request_fn(method, url, **kwargs):
        return _FakeResp(200, {"adverts": [], "next": ""})

    client = WbPromotionClient("tok", request_fn=request_fn, sleep_fn=lambda _s: None)
    try:
        get_campaign(client, 1, days=7, today=date(2026, 11, 23))
    except ValueError as exc:
        assert "не найдена" in str(exc)
    else:
        raise AssertionError("ожидали ошибку")


def test_http_list_and_detail() -> None:
    app = FastAPI()
    settings = SimpleNamespace(wb_api_token="tok")
    register_warehouse_bidder_routes(app, settings, lambda: _user())
    http = TestClient(app)

    listed = {
        "begin": "2026-11-17",
        "end": "2026-11-23",
        "days": 7,
        "status": "active",
        "campaigns": [{"advert_id": 42038717, "name": "AbrasivePrep", "has_stats": True}],
        "totals": {"spend": 1, "orders": 2},
        "count": 1,
    }
    detail = {
        "begin": "2026-10-24",
        "end": "2026-11-23",
        "days": 30,
        "campaign": {"advert_id": 42038717, "name": "AbrasivePrep", "days": []},
    }
    with (
        patch("app.web.warehouse_bidder_routes.list_campaigns", return_value=listed),
        patch("app.web.warehouse_bidder_routes.get_campaign", return_value=detail),
    ):
        resp = http.get("/api/warehouse/marketplaces/bidder/campaigns")
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 1
        one = http.get("/api/warehouse/marketplaces/bidder/campaigns/42038717")
        assert one.status_code == 200, one.text
        assert one.json()["campaign"]["advert_id"] == 42038717


def test_http_rejects_bad_period() -> None:
    app = FastAPI()
    settings = SimpleNamespace(wb_api_token="tok")
    register_warehouse_bidder_routes(app, settings, lambda: _user())
    http = TestClient(app)
    resp = http.get("/api/warehouse/marketplaces/bidder/campaigns?days=99")
    assert resp.status_code == 400


def test_bidder_permission_granted_with_marketplaces() -> None:
    perms = normalize_permissions({"marketplaces": ["repricer"]})
    assert "bidder" in perms["marketplaces"]
    full = full_access_permissions()
    assert "bidder" in full["marketplaces"]
