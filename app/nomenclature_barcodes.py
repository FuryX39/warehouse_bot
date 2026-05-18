"""Парсинг и хранение списка штрихкодов номенклатуры."""

from __future__ import annotations

import json
import re

_SPLIT_RE = re.compile(r"[,;\n|]+")


def parse_barcodes_sheet_cell(raw: str) -> list[str]:
    """ШК из ячейки листа nomenclature: несколько значений через запятую (без пробелов между кодами)."""
    if not raw or not str(raw).strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in str(raw).split(","):
        code = part.strip()
        if not code or code in seen:
            continue
        if len(code) > 128:
            code = code[:128]
        seen.add(code)
        out.append(code)
    return out


def parse_barcodes_cell(raw: str) -> list[str]:
    """Несколько ШК в одной ячейке: через запятую, точку с запятой, перевод строки."""
    if not raw or not str(raw).strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in _SPLIT_RE.split(str(raw)):
        code = part.strip()
        if not code or code in seen:
            continue
        if len(code) > 128:
            code = code[:128]
        seen.add(code)
        out.append(code)
    return out


def barcodes_to_json(codes: list[str]) -> str:
    clean: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        c = str(raw or "").strip()
        if not c or c in seen:
            continue
        if len(c) > 128:
            c = c[:128]
        seen.add(c)
        clean.append(c)
    return json.dumps(clean, ensure_ascii=False)


def barcodes_from_json(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return parse_barcodes_cell(str(raw))
    if not isinstance(data, list):
        return []
    return parse_barcodes_cell(",".join(str(x) for x in data))
