"""Тесты габаритов и объёма каталога."""

from __future__ import annotations

import pytest

from app.catalog_repository import compute_volume_liters, resolve_product_volume


def test_compute_volume_liters_from_dimensions() -> None:
    assert compute_volume_liters("100", "50", "20") == "0.1"


def test_compute_volume_liters_requires_all_dimensions() -> None:
    assert compute_volume_liters("100", "50", "") == ""


def test_resolve_product_volume_manual_overrides_dimensions() -> None:
    assert (
        resolve_product_volume("42", "100", "50", "20", volume_manual=True)
        == "42"
    )


def test_resolve_product_volume_auto_from_dimensions() -> None:
    assert (
        resolve_product_volume("", "100", "100", "100", volume_manual=False)
        == "1"
    )


def test_compute_volume_liters_rejects_negative() -> None:
    with pytest.raises(ValueError, match="отрицательной"):
        compute_volume_liters("-1", "10", "10")
