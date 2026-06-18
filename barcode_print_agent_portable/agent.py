#!/usr/bin/env python3
"""
Автономный агент тихой печати штрихкодов Code128 для панели /warehouse.

На этом компьютере: setup.bat (один раз), затем start.bat.
Панель шлёт POST http://127.0.0.1:18766/print
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.barcode_label_pdf import generate_barcode_label_pdf
from app.barcode_label_print import print_label_pdf

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("barcode_print_agent")

_HOST = os.getenv("BARCODE_PRINT_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
_PORT = int(os.getenv("BARCODE_PRINT_AGENT_PORT", "18766"))


def _load_config_env() -> None:
    for name in ("config.env", ".env"):
        env_path = _ROOT / name
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = val.strip().strip('"').strip("'")


class _Handler(BaseHTTPRequestHandler):
    server_version = "BarcodePrintAgent/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": True, "service": "barcode_print_agent"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/print":
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "Некорректный JSON"})
            return
        barcode = str(data.get("barcode") or "").strip()
        if not barcode:
            self._json(400, {"ok": False, "error": "Укажите barcode"})
            return
        sku = str(data.get("sku") or "").strip()
        name = str(data.get("name") or "").strip()
        printer = str(data.get("printer") or "").strip() or None
        try:
            copies = int(data.get("copies") or 1)
        except (TypeError, ValueError):
            copies = 1
        copies = max(1, min(9999, copies))
        try:
            for _ in range(copies):
                pdf = generate_barcode_label_pdf(barcode, sku=sku, product_name=name)
                print_label_pdf(pdf, printer=printer)
        except Exception as exc:
            logger.exception("print failed")
            self._json(500, {"ok": False, "error": str(exc)})
            return
        self._json(200, {"ok": True})


def main() -> None:
    _load_config_env()
    server = ThreadingHTTPServer((_HOST, _PORT), _Handler)
    logger.info("Агент печати: http://%s:%s (GET /health, POST /print)", _HOST, _PORT)
    if os.name == "nt":
        sumatra = os.getenv("BARCODE_PRINT_SUMATRA", "")
        if sumatra:
            logger.info("SumatraPDF: %s", sumatra)
        else:
            logger.warning("Задайте BARCODE_PRINT_SUMATRA в config.env для тихой печати на Windows")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
