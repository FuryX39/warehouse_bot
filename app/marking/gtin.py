"""Нормализация GTIN для Честного знака и каталога."""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def only_digits(value: str) -> str:
    return _DIGITS_RE.sub("", value or "")


def gs1_check_digit(body_without_check: str) -> str:
    """Контрольная цифра GS1 для тела без последней цифры."""
    total = 0
    for i, ch in enumerate(reversed(body_without_check)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return str((10 - (total % 10)) % 10)


def gtin_check_digit_ok(digits: str) -> bool:
    if not digits or not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    return gs1_check_digit(digits[:-1]) == digits[-1]


def pad_gtin14(digits: str) -> str | None:
    """Приводит 8/12/13/14 цифр к GTIN-14 без проверки контрольной цифры."""
    raw = only_digits(digits)
    if len(raw) == 14:
        return raw
    if len(raw) == 13:
        return "0" + raw
    if len(raw) == 12:
        return "00" + raw
    if len(raw) == 8:
        return "000000" + raw
    return None


def normalize_gtin14(value: str, *, require_check_digit: bool = False) -> str:
    """
    Возвращает GTIN-14.
    Пустая строка — пустой результат (поле не задано).
    Иначе ValueError с текстом для пользователя.
    """
    text = (value or "").strip()
    if not text:
        return ""
    digits = only_digits(text)
    if not digits:
        raise ValueError(f"GTIN «{text}» не содержит цифр")
    padded = pad_gtin14(digits)
    if padded is None:
        raise ValueError(
            f"GTIN «{text}» должен содержать 8, 12, 13 или 14 цифр (сейчас {len(digits)})"
        )
    if require_check_digit and not gtin_check_digit_ok(padded):
        raise ValueError(f"GTIN «{text}» не проходит проверку контрольной цифры GS1")
    return padded


def barcode_as_gtin14(barcode: str) -> str | None:
    """EAN/GTIN из штрихкода каталога: только 13 или 14 цифр."""
    digits = only_digits(barcode)
    if len(digits) not in (13, 14):
        return None
    if digits != (barcode or "").strip():
        return None
    return pad_gtin14(digits)
