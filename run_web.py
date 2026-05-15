"""
Точка входа только для веб-панели (HTTP).

Зачем отдельный файл, а не main.py:
  - main.py запускает Telegram long polling — это другой процесс и другой цикл asyncio.
  - Веб и бот можно поднять параллельно на одной БД: два процесса, один DB_URL в .env.

Как запускать:
  pip install -r requirements.txt
  python run_web.py

Переменные см. .env.example: WEB_HOST, WEB_PORT, WEB_DASHBOARD_SECRET (обязателен — пароль для входа в панель).
"""

import errno
import logging
import socket
import sys

import uvicorn

from app.bootstrap import create_inventory_stack
from app.web.server import create_dashboard_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _exit_if_port_busy(host: str, port: int) -> None:
    """Понятная ошибка вместо сырого WinError 10048, если порт уже слушает другой процесс."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
    except OSError as exc:
        in_use = exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048
        if not in_use:
            raise
        logger.error(
            "Порт %s:%s уже занят (часто не закрыт предыдущий run_web или другая программа).",
            host,
            port,
        )
        logger.error(
            "Варианты: закройте старый процесс веба; либо в .env в корне проекта задайте другой WEB_PORT."
        )
        logger.error("Проверка занятости (Windows): netstat -ano | findstr \":%s\"", port)
        sys.exit(1)


def main() -> None:
    settings, inventory_repo, coordinator, movement_repo = create_inventory_stack()
    if not (settings.web_dashboard_secret or "").strip():
        logger.error(
            "Веб-панель не запущена: в .env нужен непустой WEB_DASHBOARD_SECRET (пароль для входа в браузере)."
        )
        sys.exit(1)
    try:
        app = create_dashboard_app(settings, inventory_repo, coordinator, movement_repo)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    _exit_if_port_busy(settings.web_host, settings.web_port)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="info")


if __name__ == "__main__":
    main()
