@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Creating .venv...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv. Make sure Python is installed and added to PATH.
    pause
    exit /b 1
  )

  echo Installing dependencies (first run only)...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies from requirements.txt.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" main.py

echo Bot stopped.
pause
