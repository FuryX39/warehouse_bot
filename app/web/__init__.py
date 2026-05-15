"""
Пакет веб-интерфейса (HTTP), отдельно от Telegram-бота.

Зачем отдельная папка app/web/:
  - HTTP-слой (FastAPI) и статика (HTML/CSS/JS) лежат рядом; бот в main.py их не импортирует.
  - run_web.py только подключает create_dashboard_app() из app.web.server.

Содержимое:
  server.py   — маршруты API и раздача страницы
  static/     — стили и клиентский JS (без сборщика Node — проще деплой)
  templates/  — одна страница index.html
"""
