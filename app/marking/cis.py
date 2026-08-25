"""Разбор кода идентификации (Data Matrix / КИ Честного знака)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.marking.gtin import pad_gtin14

GS = "\x1d"
GS_PLACEHOLDER = "<GS>"

_AIM_PREFIX_RE = re.compile(r"^\][A-Za-z0-9]{2}")
_AI01_PARENS_RE = re.compile(r"\(01\)(\d{14})")
_AI21_PARENS_RE = re.compile(r"\(21\)([^()]*)")
_CRYPTO_MARK_RE = re.compile(r"91[0-9A-Za-z]{4}92")

_GS_ALIASES = (
    "\u241d",
    "<GS>",
    "[GS]",
    "{GS}",
    "\\x1d",
    "\\u001d",
    "^]",
)


@dataclass(frozen=True)
class CisRecord:
    """Разобранный КИ. serial/crypto — задел под реестр и документы ГИС МТ."""

    raw: str
    cis: str
    gtin: str
    serial: str = ""
    crypto: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.gtin) and not self.error


def replace_gs_for_excel(value: str) -> str:
    return (value or "").replace(GS, GS_PLACEHOLDER)


def restore_gs_from_excel(value: str) -> str:
    return (value or "").replace(GS_PLACEHOLDER, GS)


def _apply_gs_aliases(text: str) -> str:
    out = text.replace("\x1d", GS)
    for alias in _GS_ALIASES:
        out = out.replace(alias, GS)
    return out


def _strip_aim_prefix(text: str) -> str:
    if _AIM_PREFIX_RE.match(text):
        return text[3:]
    return text


def split_cis_input(text: str) -> list[str]:
    """Строки из textarea / файла. Не используем splitlines(): он режет по GS (U+001D)."""
    lines: list[str] = []
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            line = line.split("\t", 1)[0].strip()
            if not line:
                continue
        lines.append(line)
    return lines


def _parse_parentheses(original: str, text: str) -> CisRecord | None:
    match = _AI01_PARENS_RE.search(text)
    if not match:
        return None
    gtin = match.group(1)
    serial_m = _AI21_PARENS_RE.search(text)
    serial = (serial_m.group(1) if serial_m else "").strip()
    crypto_parts = re.findall(r"\((91|92|93)\)([^()]*)", text)
    crypto = "".join(f"{ai}{val}" for ai, val in crypto_parts if val)
    cis = f"01{gtin}"
    if serial:
        cis += f"21{serial}"
    if crypto:
        cis += GS + crypto
    return CisRecord(raw=original, cis=cis, gtin=gtin, serial=serial, crypto=crypto)


def _serial_and_crypto(after_gtin: str) -> tuple[str, str]:
    rest = after_gtin
    if rest.startswith("21"):
        rest = rest[2:]
    elif rest.startswith(GS + "21"):
        rest = rest[3:]
    else:
        return "", rest.replace(GS, "")
    if GS in rest:
        serial, tail = rest.split(GS, 1)
        return serial, tail.replace(GS, "")
    crypto_at = _CRYPTO_MARK_RE.search(rest)
    if crypto_at:
        return rest[: crypto_at.start()], rest[crypto_at.start() :]
    return rest, ""


def parse_cis(raw: str) -> CisRecord:
    original = (raw or "").strip()
    if not original:
        return CisRecord(raw="", cis="", gtin="", error="Пустая строка")

    text = _strip_aim_prefix(_apply_gs_aliases(original)).strip().lstrip(GS)
    if not text:
        return CisRecord(raw=original, cis="", gtin="", error="Пустая строка")

    paren = _parse_parentheses(original, text)
    if paren is not None:
        return paren

    if text.startswith("01") and len(text) >= 16 and text[2:16].isdigit():
        gtin = text[2:16]
        serial, crypto = _serial_and_crypto(text[16:])
        cis = f"01{gtin}"
        if serial:
            cis += f"21{serial}"
        if crypto:
            cis += GS + crypto
        return CisRecord(raw=original, cis=cis, gtin=gtin, serial=serial, crypto=crypto)

    compact = text.replace(GS, "")
    if compact.startswith("01") and len(compact) >= 16 and compact[2:16].isdigit():
        gtin = compact[2:16]
        serial, crypto = _serial_and_crypto(compact[16:])
        cis = f"01{gtin}"
        if serial:
            cis += f"21{serial}"
        if crypto:
            cis += GS + crypto
        return CisRecord(raw=original, cis=cis, gtin=gtin, serial=serial, crypto=crypto)

    maybe_digits = compact.replace(" ", "")
    if maybe_digits.isdigit() and pad_gtin14(maybe_digits) and len(maybe_digits) in (8, 12, 13, 14):
        return CisRecord(
            raw=original,
            cis="",
            gtin="",
            error="Это похоже на GTIN, а не на Data Matrix",
        )

    return CisRecord(
        raw=original,
        cis="",
        gtin="",
        error="Не удалось извлечь GTIN из Data Matrix",
    )


def parse_cis_list(lines: list[str] | str) -> list[CisRecord]:
    if isinstance(lines, str):
        lines = split_cis_input(lines)
    return [parse_cis(item) for item in lines]


def cis_identity_key(raw: str) -> str:
    """Ключ для проверки «такой код уже есть» в списке сканирования."""
    rec = parse_cis(raw)
    if rec.ok and rec.cis:
        return rec.cis
    return (raw or "").strip()
