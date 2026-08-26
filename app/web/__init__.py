"""
Пакет веб-интерфейса (HTTP), отдельно от Telegram-бота.

Зачем отдельная папка app/web/:
  - HTTP-слой (FastAPI) и статика (HTML/CSS/JS) лежат рядом; бот в main.py их не импортирует.
  - run_web.py только подключает create_dashboard_app() из app.web.server.
  - run_api.py подключает create_desktop_api_app() из app.web.desktop_api.

Содержимое:
  server.py   — маршруты панели /warehouse и /api/warehouse/*
  desktop_api.py — отдельный процесс run_api.py: /api/v1 для десктопа
  static/     — стили и клиентский JS (без сборщика Node — проще деплой)
  templates/  — одна страница index.html
"""
