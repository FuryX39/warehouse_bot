@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала запустите setup.bat
  pause
  exit /b 1
)
if not exist config.env if exist config.env.example copy /Y config.env.example config.env >nul
echo Агент печати запущен. Не закрывайте окно. Остановка: Ctrl+C
".venv\Scripts\python.exe" agent.py
if errorlevel 1 pause
