#!/usr/bin/env python3
"""
Автономный агент тихой печати штрихкодов Code128 для панели /warehouse.

На этом компьютере: setup.bat (один раз), затем start.bat.
Панель шлёт POST http://127.0.0.1:18766/print

Открывается окно статуса (tkinter). Закрытие окна останавливает агент.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import traceback
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


def _agent_host_port() -> tuple[str, int]:
    host = os.getenv("BARCODE_PRINT_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.getenv("BARCODE_PRINT_AGENT_PORT", "18766").strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 18766
    return host, max(1, min(65535, port))


def _want_gui() -> bool:
    raw = os.getenv("BARCODE_PRINT_AGENT_GUI", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "BarcodePrintAgent/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

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
        if self.path.split("?", 1)[0].rstrip("/") == "/health":
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
        try:
            copies = int(data.get("copies") or 1)
        except (TypeError, ValueError):
            copies = 1
        copies = max(1, min(9999, copies))
        try:
            for _ in range(copies):
                pdf = generate_barcode_label_pdf(barcode, sku=sku, product_name=name)
                print_label_pdf(pdf)
        except Exception as exc:
            logger.exception("print failed")
            self._json(500, {"ok": False, "error": str(exc)})
            return
        logger.info("Напечатано: barcode=%s sku=%s copies=%s", barcode, sku or "-", copies)
        self._json(200, {"ok": True})


class _QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue[str]) -> None:
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except Exception:
            pass


def _run_console(server: ThreadingHTTPServer) -> None:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        server.server_close()


def _run_gui(server: ThreadingHTTPServer, host: str, port: int) -> None:
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    log_queue: queue.Queue[str] = queue.Queue()
    handler = _QueueLogHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)

    root = tk.Tk()
    root.title("Агент печати штрихкодов")
    root.geometry("560x420")
    root.minsize(420, 320)

    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    health_url = f"http://{display_host}:{port}/health"
    printer = (os.getenv("BARCODE_PRINT_PRINTER") or "").strip() or "принтер по умолчанию"
    sumatra = (os.getenv("BARCODE_PRINT_SUMATRA") or "").strip()

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text="Агент печати запущен", font=("Segoe UI", 12, "bold")).pack(
        anchor=tk.W
    )
    ttk.Label(frame, text=f"Адрес: {health_url}").pack(anchor=tk.W, pady=(8, 0))
    ttk.Label(frame, text=f"Принтер: {printer}").pack(anchor=tk.W, pady=(4, 0))
    if sumatra:
        ttk.Label(frame, text=f"SumatraPDF: {sumatra}", wraplength=520).pack(
            anchor=tk.W, pady=(4, 0)
        )
    else:
        ttk.Label(
            frame,
            text="SumatraPDF не задан — укажите BARCODE_PRINT_SUMATRA в config.env",
            foreground="#8a5a00",
            wraplength=520,
        ).pack(anchor=tk.W, pady=(4, 0))

    ttk.Label(frame, text="Журнал:").pack(anchor=tk.W, pady=(12, 4))
    log_box = scrolledtext.ScrolledText(frame, height=14, wrap=tk.WORD, state=tk.DISABLED)
    log_box.pack(fill=tk.BOTH, expand=True)

    status_var = tk.StringVar(value="Работает")
    bottom = ttk.Frame(frame)
    bottom.pack(fill=tk.X, pady=(10, 0))
    ttk.Label(bottom, textvariable=status_var).pack(side=tk.LEFT)

    stop_requested = {"value": False}

    def append_log(line: str) -> None:
        log_box.configure(state=tk.NORMAL)
        log_box.insert(tk.END, line + "\n")
        log_box.see(tk.END)
        log_box.configure(state=tk.DISABLED)

    def drain_logs() -> None:
        while True:
            try:
                line = log_queue.get_nowait()
            except queue.Empty:
                break
            append_log(line)
        if not stop_requested["value"]:
            root.after(200, drain_logs)

    def shutdown() -> None:
        if stop_requested["value"]:
            return
        stop_requested["value"] = True
        status_var.set("Остановка...")
        threading.Thread(target=server.shutdown, daemon=True).start()
        root.after(300, root.destroy)

    def on_stop() -> None:
        shutdown()

    ttk.Button(bottom, text="Остановить", command=on_stop).pack(side=tk.RIGHT)
    root.protocol("WM_DELETE_WINDOW", shutdown)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    append_log(f"Слушаю http://{host}:{port} (GET /health, POST /print)")
    drain_logs()
    root.mainloop()
    server.server_close()
    logger.info("Остановка")


def main() -> None:
    _load_config_env()
    host, port = _agent_host_port()
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as exc:
        msg = f"Не удалось занять {host}:{port}: {exc}"
        logger.error(msg)
        if _want_gui():
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("Агент печати", msg)
                root.destroy()
            except Exception:
                pass
        raise SystemExit(1) from exc

    logger.info("Агент печати: http://%s:%s (GET /health, POST /print)", host, port)
    if os.name == "nt":
        sumatra = os.getenv("BARCODE_PRINT_SUMATRA", "")
        printer = os.getenv("BARCODE_PRINT_PRINTER", "")
        if sumatra:
            logger.info("SumatraPDF: %s", sumatra)
        else:
            logger.warning(
                "Задайте BARCODE_PRINT_SUMATRA в config.env для тихой печати на Windows"
            )
        if printer:
            logger.info("Принтер: %s", printer)

    if _want_gui():
        try:
            _run_gui(server, host, port)
            return
        except Exception:
            logger.exception("Не удалось открыть окно tkinter, работаю в консоли")
            traceback.print_exc()

    _run_console(server)


if __name__ == "__main__":
    main()
