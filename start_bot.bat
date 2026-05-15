@echo off
setlocal

REM Запуск только бота:   start_bot.bat
REM Бот + веб в отдельном окне: start_bot.bat with-web
REM (два процесса — нормально; один .bat просто стартует их по очереди.)

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv. Make sure Python is installed and added to PATH.
    pause
    exit /b 1
  )
)

echo [2/4] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo Failed to upgrade pip.
  pause
  exit /b 1
)

echo [3/4] Installing requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies from requirements.txt.
  pause
  exit /b 1
)

echo [4/4] Starting bot...
if /i "%~1"=="with-web" (
  echo       ^(also starting web panel in a separate window — close it separately^)
  start "warehouse_web" /D "%~dp0" "%~dp0.venv\Scripts\python.exe" "%~dp0run_web.py"
)
".venv\Scripts\python.exe" main.py

echo Bot stopped.
pause
