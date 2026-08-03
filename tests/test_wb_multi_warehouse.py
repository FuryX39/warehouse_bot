"""Несколько складов WB для пуша остатков."""

from __future__ import annotations

from app.adapters.wildberries import WildberriesAdapter, _parse_wb_warehouse_ids


def test_parse_wb_warehouse_ids_single_and_multi() -> None:
    assert _parse_wb_warehouse_ids("") == []
    assert _parse_wb_warehouse_ids("12345") == ["12345"]
    assert _parse_wb_warehouse_ids("123, 456;789") == ["123", "456", "789"]
    assert _parse_wb_warehouse_ids("123 456\n789") == ["123", "456", "789"]
    assert _parse_wb_warehouse_ids(["1", "1", "2"]) == ["1", "2"]


def test_adapter_accepts_multi_warehouse_ids() -> None:
    adapter = WildberriesAdapter(api_token="token", warehouse_id="111,222")
    assert adapter.warehouse_ids == ["111", "222"]
    assert adapter.warehouse_id == "111"
