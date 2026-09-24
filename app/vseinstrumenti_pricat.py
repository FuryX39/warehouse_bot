"""Формирование PRICAT ВсеИнструменты из выгрузки свободных остатков."""

from __future__ import annotations

from datetime import date
from io import BytesIO
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook

_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "vseinstrumenti_pricat.xlsx"
_SHEET_XML = "xl/worksheets/sheet1.xml"
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _article(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _free_quantity(value: Any, row: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Строка {row}: некорректное свободное количество «{value}»") from exc
    if not math.isfinite(number):
        raise ValueError(f"Строка {row}: некорректное свободное количество")
    return math.floor(number / 3)


def _patch_pricat_xml(template: bytes, values: dict[str, int | str]) -> bytes:
    """Меняет только значения ячеек, сохраняя неподдерживаемые openpyxl расширения XLSX."""
    source = BytesIO(template)
    output = BytesIO()
    with ZipFile(source, "r") as src, ZipFile(output, "w", ZIP_DEFLATED) as dst:
        if _SHEET_XML not in src.namelist():
            raise ValueError("В шаблоне PRICAT не найден XML листа «Лист 1»")
        tree = ET.fromstring(src.read(_SHEET_XML))
        cells = {
            cell.get("r"): cell
            for cell in tree.iter(f"{{{_MAIN_NS}}}c")
            if cell.get("r")
        }
        for coordinate, value in values.items():
            cell = cells.get(coordinate)
            if cell is None:
                raise ValueError(f"В шаблоне PRICAT отсутствует ячейка {coordinate}")
            for child in list(cell):
                if child.tag in {f"{{{_MAIN_NS}}}v", f"{{{_MAIN_NS}}}is", f"{{{_MAIN_NS}}}f"}:
                    cell.remove(child)
            if isinstance(value, str):
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{_MAIN_NS}}}is")
                text = ET.SubElement(inline, f"{{{_MAIN_NS}}}t")
                text.text = value
            else:
                cell.attrib.pop("t", None)
                node = ET.SubElement(cell, f"{{{_MAIN_NS}}}v")
                node.text = str(value)
        for item in src.infolist():
            data = (
                ET.tostring(tree, encoding="utf-8", xml_declaration=True)
                if item.filename == _SHEET_XML
                else src.read(item.filename)
            )
            dst.writestr(item, data)
    return output.getvalue()


def build_vseinstrumenti_pricat(
    stock_bytes: bytes,
    template_bytes: bytes | None = None,
    *,
    actual_date: date | None = None,
) -> tuple[bytes, dict[str, int]]:
    """Заполнить PRICAT: A/E входа — артикул/свободно, E/AB шаблона — артикул/остаток."""
    try:
        stock_book = load_workbook(BytesIO(stock_bytes), data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError("Не удалось прочитать файл остатков Excel") from exc
    stock_sheet = stock_book.active
    quantities: dict[str, int] = {}
    for row in range(1, stock_sheet.max_row + 1):
        article = _article(stock_sheet.cell(row, 1).value)
        free = stock_sheet.cell(row, 5).value
        if not article:
            continue
        # Допускается строка заголовков в будущих выгрузках.
        if article.casefold() in {"артикул", "article", "sku"}:
            continue
        if free in (None, ""):
            continue
        quantities[article.casefold()] = _free_quantity(free, row)

    if not quantities:
        raise ValueError("В файле остатков не найдены артикулы и свободное количество")

    try:
        template = template_bytes if template_bytes is not None else _TEMPLATE_PATH.read_bytes()
        template_book = load_workbook(BytesIO(template))
    except Exception as exc:
        raise ValueError("Не удалось прочитать шаблон PRICAT Excel") from exc
    if "Лист 1" not in template_book.sheetnames:
        raise ValueError("В PRICAT отсутствует лист «Лист 1»")
    sheet = template_book["Лист 1"]
    if _article(sheet["E12"].value).casefold() != "артикул":
        raise ValueError("В PRICAT не найден столбец «Артикул» в E12")
    if _article(sheet["AB12"].value).casefold() != "остаток на складе":
        raise ValueError("В PRICAT не найден столбец «Остаток на складе» в AB12")

    matched: set[str] = set()
    updates: dict[str, int | str] = {}
    updated = 0
    for row in range(13, sheet.max_row + 1):
        key = _article(sheet.cell(row, 5).value).casefold()
        if not key or key not in quantities:
            continue
        updates[f"AB{row}"] = quantities[key]
        matched.add(key)
        updated += 1

    current = actual_date or date.today()
    updates["C6"] = f"Актуальные остатки {current:%d.%m}"
    template_book.close()
    return _patch_pricat_xml(template, updates), {
        "source_articles": len(quantities),
        "updated": updated,
        "not_found_in_pricat": len(quantities) - len(matched),
    }
