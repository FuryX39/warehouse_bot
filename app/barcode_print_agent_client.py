"""HTTP-клиент локального агента печати штрихкодов (для прокси с веб-сервера)."""

from __future__ import annotations

import os

import requests

_DEFAULT_PORT = 18766
_TIMEOUT_HEALTH = 2.0
_TIMEOUT_PRINT = 120.0


def barcode_print_agent_port() -> int:
    raw = (os.getenv("BARCODE_PRINT_AGENT_PORT") or str(_DEFAULT_PORT)).strip()
    try:
        n = int(raw)
    except ValueError:
        n = _DEFAULT_PORT
    return max(1, min(65535, n))


def barcode_print_agent_host() -> str:
    return (os.getenv("BARCODE_PRINT_AGENT_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def barcode_print_agent_base_url() -> str:
    host = barcode_print_agent_host()
    return f"http://{host}:{barcode_print_agent_port()}"


def barcode_print_agent_health() -> dict:
    url = f"{barcode_print_agent_base_url()}/health"
    try:
        resp = requests.get(url, timeout=_TIMEOUT_HEALTH)
        if resp.ok:
            data = resp.json() if resp.content else {}
            if isinstance(data, dict) and data.get("ok"):
                return {"ok": True, "service": data.get("service", "barcode_print_agent")}
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def barcode_print_agent_print(payload: dict) -> dict:
    url = f"{barcode_print_agent_base_url()}/print"
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT_PRINT)
        data = resp.json() if resp.content else {}
        if resp.ok and isinstance(data, dict) and data.get("ok"):
            return {"ok": True}
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error") or "")
        if not err:
            err = f"HTTP {resp.status_code}"
        return {"ok": False, "error": err}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
