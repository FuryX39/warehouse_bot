@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Создайте venv: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
REM Для тихой печати PDF на Windows установите SumatraPDF и раскомментируйте строку ниже:
REM set BARCODE_PRINT_SUMATRA=C:\Program Files\SumatraPDF\SumatraPDF.exe
".venv\Scripts\python.exe" tools\barcode_print_agent.py
