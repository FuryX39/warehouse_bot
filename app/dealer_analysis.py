"""Сравнение двух Excel-отчётов по заказам дилера (артикул / кол-во / цены)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import BinaryIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

_HEADER_HINTS = frozenset(
    {
        "артикул",
        "sku",
        "код",
        "наименование",
        "количество",
        "кол-во",
        "кол",
        "qty",
        "quantity",
        "цена",
        "сумма",
        "итого",
    }
)


@dataclass(frozen=True)
class DealerPeriodLine:
    sku: str
    quantity: float
    unit_price: float
    line_total: float


@dataclass(frozen=True)
class DealerComparisonRow:
    sku: str
    qty_a: float
    qty_b: float
    unit_price_a: float | None
    unit_price_b: float | None
    total_a: float
    total_b: float
    trend: str
    trend_detail: str


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cell_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _row_looks_like_header(cells: tuple[object, object, object, object]) -> bool:
    joined = " ".join(_cell_str(c).lower() for c in cells)
    if not joined:
        return False
    if any(h in joined for h in _HEADER_HINTS):
        return True
    # Первая ячейка текст, вторая не число — вероятно заголовок.
    if cells[0] is not None and _cell_float(cells[1]) is None and _cell_str(cells[0]):
        return bool(re.search(r"[а-яa-z]", _cell_str(cells[0]).lower()))
    return False


def parse_dealer_orders_excel(source: bytes | BinaryIO) -> dict[str, DealerPeriodLine]:
    """Читает xlsx: колонки A–D — артикул, кол-во, цена/шт, сумма. Дубликаты SKU суммируются."""
    buf = io.BytesIO(source) if isinstance(source, bytes) else source
    wb = load_workbook(filename=buf, read_only=True, data_only=True)
    try:
        ws = wb.active
        sku_display: dict[str, str] = {}
        aggregated: dict[str, tuple[float, float]] = {}
        for row in ws.iter_rows(min_row=1, max_col=4, values_only=True):
            cells = (
                row[0] if len(row) > 0 else None,
                row[1] if len(row) > 1 else None,
                row[2] if len(row) > 2 else None,
                row[3] if len(row) > 3 else None,
            )
            if _row_looks_like_header(cells):
                continue
            sku = _cell_str(cells[0])
            if not sku:
                continue
            qty = _cell_float(cells[1])
            if qty is None or qty <= 0:
                continue
            unit_price = _cell_float(cells[2])
            line_total = _cell_float(cells[3])
            if line_total is None and unit_price is not None:
                line_total = unit_price * qty
            elif line_total is None:
                line_total = 0.0
            key = sku.casefold()
            sku_display[key] = sku
            prev_q, prev_t = aggregated.get(key, (0.0, 0.0))
            aggregated[key] = (prev_q + qty, prev_t + float(line_total))
        out: dict[str, DealerPeriodLine] = {}
        for key, (q, t) in aggregated.items():
            display = sku_display[key]
            out[display] = DealerPeriodLine(
                sku=display,
                quantity=q,
                unit_price=(t / q) if q else 0.0,
                line_total=t,
            )
        return out
    finally:
        wb.close()


def _pct_change(old: float, new: float) -> float | None:
    if old == 0:
        return None if new == 0 else 100.0
    return ((new - old) / old) * 100.0


def _classify_trend(qty_a: float, qty_b: float, total_a: float, total_b: float) -> tuple[str, str]:
    if qty_a <= 0 and qty_b > 0:
        return "Новая позиция", "Не продавалась в первом периоде"
    if qty_b <= 0 and qty_a > 0:
        return "Снята с продаж", "Была в первом периоде, во втором нет"
    if qty_a > 0 and qty_b > 0:
        if qty_b > qty_a:
            rev = ""
            if total_b > total_a:
                rev = ", выручка выросла"
            elif total_b < total_a:
                rev = ", выручка упала при росте кол-ва"
            return "Рост", f"Количество: {qty_a:g} → {qty_b:g}{rev}"
        if qty_b < qty_a:
            rev = ""
            if total_b < total_a:
                rev = ", выручка упала"
            elif total_b > total_a:
                rev = ", выручка выросла при падении кол-ва"
            return "Падение", f"Количество: {qty_a:g} → {qty_b:g}{rev}"
        if total_b > total_a:
            return "Без изменений кол-ва", "Выручка выросла при том же количестве"
        if total_b < total_a:
            return "Без изменений кол-ва", "Выручка упала при том же количестве"
        return "Без изменений", "Количество и выручка совпали"
    return "—", ""


def compare_dealer_periods(
    period_a: dict[str, DealerPeriodLine],
    period_b: dict[str, DealerPeriodLine],
) -> list[DealerComparisonRow]:
    keys_a = {k.casefold(): k for k in period_a}
    keys_b = {k.casefold(): k for k in period_b}
    all_keys = sorted(set(keys_a) | set(keys_b))
    rows: list[DealerComparisonRow] = []
    for key in all_keys:
        sku = keys_b.get(key) or keys_a[key]
        la = period_a.get(keys_a[key]) if key in keys_a else None
        lb = period_b.get(keys_b[key]) if key in keys_b else None
        qty_a = la.quantity if la else 0.0
        qty_b = lb.quantity if lb else 0.0
        total_a = la.line_total if la else 0.0
        total_b = lb.line_total if lb else 0.0
        unit_a = la.unit_price if la and la.quantity else None
        unit_b = lb.unit_price if lb and lb.quantity else None
        trend, detail = _classify_trend(qty_a, qty_b, total_a, total_b)
        rows.append(
            DealerComparisonRow(
                sku=sku,
                qty_a=qty_a,
                qty_b=qty_b,
                unit_price_a=unit_a,
                unit_price_b=unit_b,
                total_a=total_a,
                total_b=total_b,
                trend=trend,
                trend_detail=detail,
            )
        )
    return rows


_TREND_SORT = {
    "Снята с продаж": 0,
    "Новая позиция": 1,
    "Падение": 2,
    "Рост": 3,
    "Без изменений кол-ва": 4,
    "Без изменений": 5,
    "—": 6,
}


def build_comparison_stats(rows: list[DealerComparisonRow]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.trend] = counts.get(r.trend, 0) + 1
    return {
        "sku_count": len(rows),
        "only_period_a": counts.get("Снята с продаж", 0),
        "only_period_b": counts.get("Новая позиция", 0),
        "growth": counts.get("Рост", 0),
        "decline": counts.get("Падение", 0),
        "unchanged": counts.get("Без изменений", 0) + counts.get("Без изменений кол-ва", 0),
        "by_trend": counts,
    }


def export_comparison_excel(
    rows: list[DealerComparisonRow],
    *,
    period_a_label: str,
    period_b_label: str,
) -> bytes:
    label_a = period_a_label or "Период A"
    label_b = period_b_label or "Период B"
    sorted_rows = sorted(
        rows,
        key=lambda r: (_TREND_SORT.get(r.trend, 99), r.sku.casefold()),
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Анализ"
    headers = [
        "Артикул",
        "Тренд",
        "Комментарий",
        f"Кол-во ({label_a})",
        f"Кол-во ({label_b})",
        "Δ кол-во",
        "Δ кол-во %",
        f"Цена/шт ({label_a})",
        f"Цена/шт ({label_b})",
        f"Сумма ({label_a})",
        f"Сумма ({label_b})",
        "Δ сумма",
        "Δ сумма %",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for r in sorted_rows:
        dq = r.qty_b - r.qty_a
        dt = r.total_b - r.total_a
        pct_q = _pct_change(r.qty_a, r.qty_b)
        pct_t = _pct_change(r.total_a, r.total_b)
        ws.append(
            [
                r.sku,
                r.trend,
                r.trend_detail,
                r.qty_a or "",
                r.qty_b or "",
                dq if (r.qty_a or r.qty_b) else "",
                round(pct_q, 1) if pct_q is not None else "",
                round(r.unit_price_a, 2) if r.unit_price_a is not None else "",
                round(r.unit_price_b, 2) if r.unit_price_b is not None else "",
                round(r.total_a, 2) if r.total_a else "",
                round(r.total_b, 2) if r.total_b else "",
                round(dt, 2) if (r.total_a or r.total_b) else "",
                round(pct_t, 1) if pct_t is not None else "",
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_dealer_analysis(
    file_a: bytes,
    file_b: bytes,
    *,
    period_a_label: str,
    period_b_label: str,
) -> tuple[list[DealerComparisonRow], dict, bytes]:
    period_a = parse_dealer_orders_excel(file_a)
    period_b = parse_dealer_orders_excel(file_b)
    rows = compare_dealer_periods(period_a, period_b)
    stats = build_comparison_stats(rows)
    stats["period_a_skus"] = len(period_a)
    stats["period_b_skus"] = len(period_b)
    report_xlsx = export_comparison_excel(rows, period_a_label=period_a_label, period_b_label=period_b_label)
    return rows, stats, report_xlsx
