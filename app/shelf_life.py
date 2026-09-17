"""Срок годности: дата производства + срок в годах → дата окончания."""

from __future__ import annotations

from datetime import date, datetime


def parse_production_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Некорректная дата производства: {text}")


def add_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + int(years))
    except ValueError:
        return value.replace(year=value.year + int(years), day=28)


def format_expiry_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def expiry_from_production(production_date: object, *, years: int) -> str:
    produced = parse_production_date(production_date)
    if produced is None:
        raise ValueError("Укажите дату производства")
    if int(years) < 1:
        raise ValueError("Укажите срок годности в годах")
    return format_expiry_date(add_calendar_years(produced, int(years)))
