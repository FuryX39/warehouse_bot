"""
Точка входа внешнего API для десктоп-программ (упаковщики, задачи).

Отдельно от:
  - main.py — Telegram-бот
  - run_web.py — браузерная панель /warehouse и /api/warehouse/*

Общая БД (DB_URL) и каталог файлов заданий. Процесс не крутит синк маркетплейсов
и не отдаёт HTML панели — только /api/v1.

Как запускать:
  python run_api.py

Переменные: API_HOST, API_PORT (по умолчанию 127.0.0.1:8766).
Вход упаковщика: POST /api/v1/login  {"login": "...", "password": "..."}
"""

import errno
import logging
import socket
import sys
import traceback

import uvicorn

from app.config import load_settings
from app.web.desktop_api import create_desktop_api_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _check_runtime_dependencies() -> None:
    try:
        import sqlalchemy
    except ImportError as exc:
        logger.error("Не установлен SQLAlchemy: %s", exc)
        logger.error("На сервере выполните: %s -m pip install -r requirements.txt", sys.executable)
        sys.exit(1)
    version = str(getattr(sqlalchemy, "__version__", "0") or "0")
    parts = version.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2].split("+", 1)[0])
    except (ValueError, IndexError):
        return
    if (major, minor, patch) < (2, 0, 41):
        logger.error(
            "SQLAlchemy %s слишком старый; нужен >= 2.0.41. "
            "Выполните: %s -m pip install -r requirements.txt",
            version,
            sys.executable,
        )
        sys.exit(1)


def _exit_if_port_busy(host: str, port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError as exc:
        in_use = exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048
        if not in_use:
            raise
        logger.error(
            "Порт %s:%s уже занят (часто не закрыт предыдущий run_api).",
            host,
            port,
        )
        logger.error(
            "Варианты: закройте старый процесс API; либо в .env задайте другой API_PORT."
        )
        sys.exit(1)


def main() -> None:
    _check_runtime_dependencies()
    try:
        settings = load_settings()
    except Exception as exc:
        logger.error("Не удалось прочитать настройки: %s", exc)
        logger.error(traceback.format_exc())
        sys.exit(1)
    try:
        app = create_desktop_api_app(settings)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Не удалось создать API: %s", exc)
        logger.error(traceback.format_exc())
        sys.exit(1)
    _exit_if_port_busy(settings.api_host, settings.api_port)
    logger.info(
        "Desktop API: http://%s:%s/api/v1/ (вход: POST /api/v1/login, упаковка: /api/v1/fbs-packing)",
        settings.api_host,
        settings.api_port,
    )
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
