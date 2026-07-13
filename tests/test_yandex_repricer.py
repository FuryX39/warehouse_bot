"""Тесты репрайсера Яндекс Маркет."""

from __future__ import annotations

from app.yandex_repricer import (
    card_from_showcase,
    seller_from_showcase,
    seller_from_target_card,
    showcase_from_seller,
)


def test_showcase_and_card_formulas() -> None:
    assert showcase_from_seller(300) == 254
    assert card_from_showcase(253) == 182
    assert showcase_from_seller(1239) == 754
    assert card_from_showcase(754) == 670


def test_seller_from_target_card_inverts_chain() -> None:
    target = 599
    recommended = seller_from_target_card(target)
    showcase = showcase_from_seller(recommended)
    card = card_from_showcase(showcase)
    assert abs(card - target) <= 1


def test_seller_from_showcase_breakpoint() -> None:
    assert seller_from_showcase(253) < 500
    assert seller_from_showcase(754) >= 500
