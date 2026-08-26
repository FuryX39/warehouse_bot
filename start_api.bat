@echo off
setlocal

REM Запускайте после start_bot.bat (нужен тот же .venv и установленные зависимости).
REM Отдельный процесс API для программ упаковщиков: python run_api.py
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Сначала запустите start_bot.bat — нужен каталог .venv и pip install.
  pause
  exit /b 1
)

echo Запуск desktop API (run_api.py)...
echo По умолчанию http://127.0.0.1:8766/api/v1/  (API_HOST / API_PORT в .env)
echo Останов: закройте это окно или Ctrl+C.
echo.

".venv\Scripts\python.exe" run_api.py

echo.
echo Desktop API остановлен.
pause
