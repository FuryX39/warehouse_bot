"""Чтение и запись .env из админ-панели."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = _PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = _PROJECT_ROOT / ".env.example"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EnvField:
    key: str
    value: str
    comment: str = ""
    in_env: bool = False
    in_example: bool = False


def _parse_env_lines(path: Path) -> tuple[dict[str, str], dict[str, str], list[str]]:
    values: dict[str, str] = {}
    comments: dict[str, str] = {}
    order: list[str] = []
    pending_comments: list[str] = []
    if not path.is_file():
        return values, comments, order

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            pending_comments = []
            continue
        if line.startswith("#"):
            pending_comments.append(line.lstrip("#").strip())
            # Also support commented template vars: # KEY=value
            maybe = line[1:].strip()
            if "=" not in maybe:
                continue
            key, _, val = maybe.partition("=")
            key = key.strip()
            if not _KEY_RE.match(key):
                continue
            if key not in values:
                values[key] = val.strip().strip('"').strip("'")
                comments[key] = "\n".join(c for c in pending_comments[:-1] if c)
                order.append(key)
            continue
        if "=" not in line:
            pending_comments = []
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            pending_comments = []
            continue
        values[key] = val.strip().strip('"').strip("'")
        if key not in order:
            order.append(key)
        if pending_comments:
            comments[key] = "\n".join(c for c in pending_comments if c)
        pending_comments = []
    return values, comments, order


def read_env_fields() -> list[dict[str, Any]]:
    env_values, env_comments, env_order = _parse_env_lines(ENV_PATH)
    example_values, example_comments, example_order = _parse_env_lines(ENV_EXAMPLE_PATH)

    keys: list[str] = []
    seen: set[str] = set()
    for key in example_order + env_order:
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)

    fields: list[EnvField] = []
    for key in keys:
        fields.append(
            EnvField(
                key=key,
                value=env_values.get(key, ""),
                comment=env_comments.get(key) or example_comments.get(key, ""),
                in_env=key in env_values,
                in_example=key in example_values,
            )
        )
    return [field.__dict__ for field in fields]


def _quote_env_value(value: str) -> str:
    s = str(value or "")
    if s == "":
        return ""
    if any(ch in s for ch in ("\n", "\r")):
        s = s.replace("\r", "").replace("\n", "\\n")
    if s.startswith(" ") or s.endswith(" ") or "#" in s or '"' in s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def write_env_fields(fields: list[dict[str, Any]]) -> None:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in fields:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        if not _KEY_RE.match(key):
            raise ValueError(f"Некорректное имя переменной: {key}")
        if key in seen:
            raise ValueError(f"Дублируется переменная: {key}")
        seen.add(key)
        normalized.append((key, str(item.get("value") or "")))

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Файл сохранён из админ-панели /warehouse.",
        "# После изменения ключей API, DB_URL, портов и веб-настроек перезапустите сервис.",
        "",
    ]
    for key, value in normalized:
        lines.append(f"{key}={_quote_env_value(value)}")

    content = "\n".join(lines).rstrip() + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(ENV_PATH.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        tmp_path.replace(ENV_PATH)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    load_dotenv(ENV_PATH, override=True)
