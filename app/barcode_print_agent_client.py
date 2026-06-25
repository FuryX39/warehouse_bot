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


def _is_local_host(host: str) -> bool:
    h = str(host or "").strip().lower()
    return h in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}


def barcode_print_agent_candidates(*, client_host: str = "") -> list[str]:
    configured = barcode_print_agent_host()
    candidates: list[str] = []
    if configured and configured != "0.0.0.0":
        candidates.append(configured)
    client = str(client_host or "").strip()
    if client and client != "0.0.0.0" and client not in candidates:
        # If no explicit remote host is configured, the browser user's IP is the
        # most likely machine where the local print agent is running.
        if _is_local_host(configured) or client != configured:
            candidates.append(client)
    for fallback in ("127.0.0.1", "localhost"):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def barcode_print_agent_base_url(*, host: str | None = None) -> str:
    host = host or barcode_print_agent_host()
    return f"http://{host}:{barcode_print_agent_port()}"


def barcode_print_agent_health(*, client_host: str = "") -> dict:
    errors: list[str] = []
    for host in barcode_print_agent_candidates(client_host=client_host):
        url = f"{barcode_print_agent_base_url(host=host)}/health"
        try:
            resp = requests.get(url, timeout=_TIMEOUT_HEALTH)
            if resp.ok:
                data = resp.json() if resp.content else {}
                if isinstance(data, dict) and data.get("ok"):
                    return {
                        "ok": True,
                        "service": data.get("service", "barcode_print_agent"),
                        "host": host,
                        "port": barcode_print_agent_port(),
                    }
            errors.append(f"{host}: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{host}: {exc}")
    return {"ok": False, "error": "; ".join(errors)}


def barcode_print_agent_print(payload: dict, *, client_host: str = "") -> dict:
    errors: list[str] = []
    for host in barcode_print_agent_candidates(client_host=client_host):
        url = f"{barcode_print_agent_base_url(host=host)}/print"
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_PRINT)
            data = resp.json() if resp.content else {}
            if resp.ok and isinstance(data, dict) and data.get("ok"):
                return {"ok": True, "host": host, "port": barcode_print_agent_port()}
            err = ""
            if isinstance(data, dict):
                err = str(data.get("error") or "")
            if not err:
                err = f"HTTP {resp.status_code}"
            errors.append(f"{host}: {err}")
        except requests.RequestException as exc:
            errors.append(f"{host}: {exc}")
    return {"ok": False, "error": "; ".join(errors)}
