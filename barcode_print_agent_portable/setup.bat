@echo off
cd /d "%~dp0"
echo === Установка агента печати штрихкодов ===
where python >nul 2>&1
if errorlevel 1 (
  echo Нужен Python 3.10+ в PATH: https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Создание виртуального окружения...
  python -m venv .venv
  if errorlevel 1 (
    echo Ошибка создания venv
    pause
    exit /b 1
  )
)
echo Установка зависимостей...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
  echo Ошибка установки пакетов
  pause
  exit /b 1
)
if not exist config.env (
  if exist config.env.example copy /Y config.env.example config.env >nul
  echo Создан config.env — укажите путь к SumatraPDF при необходимости.
)
echo.
echo Готово. Запуск: start.bat
pause
