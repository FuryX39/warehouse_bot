"""Кратковременный кэш PDF-этикеток FBS после генерации (для скачивания из веба)."""

from __future__ import annotations

import secrets
import time
from threading import Lock

_TTL_SECONDS = 600
_lock = Lock()
_store: dict[str, tuple[float, list[tuple[str, bytes]]]] = {}


def _purge_locked(now: float) -> None:
    expired = [k for k, (ts, _) in _store.items() if now - ts > _TTL_SECONDS]
    for k in expired:
        del _store[k]


def store_label_files(label_files: list[tuple[str, bytes]]) -> str:
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        _purge_locked(now)
        _store[token] = (now, list(label_files))
    return token


def pop_label_files(token: str) -> list[tuple[str, bytes]] | None:
    key = (token or "").strip()
    if not key:
        return None
    now = time.time()
    with _lock:
        _purge_locked(now)
        entry = _store.pop(key, None)
    if entry is None:
        return None
    ts, files = entry
    if now - ts > _TTL_SECONDS:
        return None
    return files
