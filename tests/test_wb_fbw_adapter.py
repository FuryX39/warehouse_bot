"""Чтение FBW supplies-api: fallback isPreorderID и ошибки токена Поставки."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.adapters.wildberries import WildberriesAdapter, _parse_fbw_supply_id, _wb_request


def test_parse_fbw_supply_id_rejects_gi() -> None:
    with pytest.raises(ValueError, match="числовой ID"):
        _parse_fbw_supply_id("WB-GI-277689956")


def test_wb_request_supports_patch() -> None:
    expected = _resp(ok=True, status=204)
    with patch("app.adapters.wildberries.requests.patch", return_value=expected) as request:
        actual = _wb_request("PATCH", "https://example/orders", json={"orders": [1]})
    assert actual is expected
    request.assert_called_once_with("https://example/orders", json={"orders": [1]})


def _resp(*, ok: bool, status: int, payload=None, text: str = "", url: str = "https://example"):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.reason = "Error" if not ok else "OK"
    resp.url = url
    resp.text = text
    resp.json.return_value = payload
    return resp


def test_fetch_fbw_supply_retries_preorder_on_404() -> None:
    adapter = WildberriesAdapter(api_token="test-token")
    calls: list[dict | None] = []

    def fake_request(method, url, **kwargs):
        params = kwargs.get("params")
        calls.append(params)
        if params and params.get("isPreorderID") == "true":
            return _resp(ok=True, status=200, payload={"supplyID": 41357389, "statusID": 2})
        return _resp(
            ok=False,
            status=404,
            text='{"detail":"not found"}',
            url=url,
        )

    with patch("app.adapters.wildberries._wb_request", side_effect=fake_request):
        data = adapter.fetch_fbw_supply("41357389")
    assert data["supplyID"] == 41357389
    assert calls[0] is None
    assert calls[1] == {"isPreorderID": "true"}


def test_fetch_fbw_supply_does_not_retry_preorder_on_400() -> None:
    adapter = WildberriesAdapter(api_token="test-token")
    calls: list[dict | None] = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs.get("params"))
        return _resp(
            ok=False,
            status=400,
            text='{"detail":"поставка удалена"}',
            url=url,
        )

    with patch("app.adapters.wildberries._wb_request", side_effect=fake_request):
        with pytest.raises(requests.HTTPError, match="удалена"):
            adapter.fetch_fbw_supply("41357389")
    assert calls == [None]


def test_fbw_auth_error_mentions_supplies_category() -> None:
    adapter = WildberriesAdapter(api_token="test-token")

    def fake_request(method, url, **kwargs):
        return _resp(ok=False, status=401, text="unauthorized", url=url)

    with patch("app.adapters.wildberries._wb_request", side_effect=fake_request):
        with pytest.raises(requests.HTTPError, match="Поставки"):
            adapter.fetch_fbw_supply_packages("41357389")
