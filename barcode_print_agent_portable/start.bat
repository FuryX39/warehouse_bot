@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала запустите setup.bat
  pause
  exit /b 1
)
echo Агент печати запущен. Не закрывайте окно. Остановка: Ctrl+C
".venv\Scripts\python.exe" agent.py
if errorlevel 1 pause
