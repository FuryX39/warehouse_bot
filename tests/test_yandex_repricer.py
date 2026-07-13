"""Тесты репрайсера Яндекс Маркет."""

from __future__ import annotations

from app.yandex_repricer import (
    _needs_reprice,
    card_from_seller,
    card_from_showcase,
    seller_from_showcase,
    seller_from_target_card,
    showcase_from_seller,
)


def test_needs_reprice_thresholds() -> None:
    catalog = 1000.0
    assert not _needs_reprice(901, catalog)
    assert _needs_reprice(900, catalog)
    assert not _needs_reprice(1000, catalog)
    assert not _needs_reprice(1299, catalog)
    assert _needs_reprice(1300, catalog)


def test_seller_from_target_card_never_undershoots_on_raise() -> None:
    for target in range(50, 2500, 7):
        recommended = seller_from_target_card(float(target), raise_price=True)
        assert card_from_seller(recommended) >= target


def test_seller_from_target_card_never_overshoots_on_lower() -> None:
    for target in range(50, 2500, 7):
        current = seller_from_target_card(float(target), raise_price=True) + 50
        recommended = seller_from_target_card(
            float(target),
            current_seller=current,
            raise_price=False,
        )
        assert card_from_seller(recommended) <= target


def test_seller_from_target_card_raise_respects_current_seller() -> None:
    recommended = seller_from_target_card(300.0, current_seller=400, raise_price=True)
    assert recommended >= 400
    assert card_from_seller(recommended) >= 300


def test_seller_from_target_card_fixes_known_undershoot() -> None:
    recommended = seller_from_target_card(202.0, raise_price=True)
    assert card_from_seller(recommended) >= 202
    assert recommended == 331

def test_showcase_and_card_formulas() -> None:
    assert showcase_from_seller(300) == 254
    assert card_from_showcase(253) == 182
    assert showcase_from_seller(1239) == 754
    assert card_from_showcase(754) == 670


def test_seller_from_target_card_inverts_chain() -> None:
    target = 599
    recommended = seller_from_target_card(target, raise_price=True)
    assert card_from_seller(recommended) >= target


def test_seller_from_showcase_breakpoint() -> None:
    assert seller_from_showcase(253) < 500
    assert seller_from_showcase(754) >= 500
