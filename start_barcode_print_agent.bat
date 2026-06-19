@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Создайте venv: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
REM Для тихой печати PDF на Windows установите SumatraPDF и раскомментируйте в config.env:
REM BARCODE_PRINT_SUMATRA=C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe
REM BARCODE_PRINT_PRINTER=имя принтера
if not exist config.env if exist config.env.example copy /Y config.env.example config.env >nul
echo Агент печати штрихкодов запущен. Окно не закрывайте. Остановка: Ctrl+C
".venv\Scripts\python.exe" tools\barcode_print_agent.py
if errorlevel 1 pause
