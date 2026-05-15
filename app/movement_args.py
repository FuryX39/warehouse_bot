"""Разбор аргументов команд /movement и /movement_edit."""

from __future__ import annotations


def _looks_like_sheet_url(token: str) -> bool:
    t = token.strip().lower()
    return t.startswith("http://") or t.startswith("https://") or "docs.google.com" in t or "/spreadsheets/" in t


def parse_movement_flags(args: list[str]) -> tuple[str | None, str | None, list[str]]:
    """
    Извлекает --name / -n и --comment / -c из хвоста аргументов.
    Возвращает (name, comment, оставшиеся токены).
    """
    name: str | None = None
    comment: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        low = tok.lower()
        if low in ("--name", "-n", "--название", "-название"):
            i += 1
            if i >= len(args):
                break
            name = " ".join(args[i:]).strip()
            break
        if low in ("--comment", "-c", "--комментарий", "-комментарий"):
            i += 1
            if i >= len(args):
                comment = ""
                break
            comment = " ".join(args[i:]).strip()
            break
        rest.append(tok)
        i += 1
    return name, comment, rest


def parse_movement_command_args(
    args: list[str],
) -> tuple[int | None, str | None, str | None, str | None, str | None]:
    """
    /movement НАПРАВЛЕНИЕ [URL] [--name ...] [--comment ...]
    comment=None если флаг не указан (пустой комментарий при создании).
  """
    if not args:
        return None, None, None, None, "no_args"
    sign = None  # caller parses direction
    name, comment, rest = parse_movement_flags(args[1:])
    sheet_url: str | None = None
    url_parts: list[str] = []
    for tok in rest:
        if sheet_url is None and _looks_like_sheet_url(tok):
            sheet_url = tok.strip()
        elif sheet_url is None and url_parts:
            url_parts.append(tok)
            candidate = " ".join(url_parts).strip()
            if _looks_like_sheet_url(candidate):
                sheet_url = candidate
                url_parts = []
        elif sheet_url is None:
            url_parts.append(tok)
        # лишние токены без URL игнорируем
    if sheet_url is None and url_parts:
        candidate = " ".join(url_parts).strip()
        if _looks_like_sheet_url(candidate):
            sheet_url = candidate
    return sign, sheet_url, name, comment, None


def parse_movement_edit_args(
    args: list[str],
) -> tuple[int | None, str | None, str | None, bool, str | None]:
    """
    /movement_edit ID [--name ...] [--comment ...]
    Возвращает (id, name, comment, comment_given, error).
    name/comment: None = не менять; comment_given=True если был флаг --comment (в т.ч. пустой).
    """
    if not args:
        return None, None, None, False, "no_args"
    try:
        movement_id = int(args[0].strip())
    except ValueError:
        return None, None, None, False, "bad_id"
    if movement_id <= 0:
        return None, None, None, False, "bad_id"

    name: str | None = None
    comment: str | None = None
    comment_given = False
    i = 1
    while i < len(args):
        tok = args[i]
        low = tok.lower()
        if low in ("--name", "-n", "--название", "-название"):
            i += 1
            name = " ".join(args[i:]).strip() if i < len(args) else ""
            break
        if low in ("--comment", "-c", "--комментарий", "-комментарий"):
            i += 1
            comment_given = True
            comment = " ".join(args[i:]).strip() if i < len(args) else ""
            break
        i += 1
    if name is None and not comment_given:
        return movement_id, None, None, False, "nothing_to_update"
    return movement_id, name, comment, comment_given, None
