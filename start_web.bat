@echo off
setlocal

REM Запускайте после start_bot.bat (нужен тот же .venv и установленные зависимости).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Сначала запустите start_bot.bat — нужен каталог .venv и pip install.
  pause
  exit /b 1
)

echo Запуск веб-панели (run_web.py)...
echo Нужен WEB_DASHBOARD_SECRET в .env — пароль для входа в браузере.
echo Останов: закройте это окно или Ctrl+C.
echo.

".venv\Scripts\python.exe" run_web.py

echo.
echo Веб-панель остановлена.
pause
