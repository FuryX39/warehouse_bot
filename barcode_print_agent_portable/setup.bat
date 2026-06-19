@echo off
cd /d "%~dp0"
set "QUIET=0"
if /I "%~1"=="/q" set "QUIET=1"
if /I "%~1"=="--quiet" set "QUIET=1"

echo === Установка агента печати штрихкодов ===

set "PYBOOT="
where py >nul 2>&1 && (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PYBOOT=py -3"
)
if not defined PYBOOT (
  where python >nul 2>&1 && (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYBOOT=python"
  )
)
if not defined PYBOOT (
  echo Нужен Python 3.10+ в PATH: https://www.python.org/downloads/
  echo При установке отметьте «Add python.exe to PATH».
  if "%QUIET%"=="0" pause
  exit /b 1
)

set "NEED_VENV=1"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if not errorlevel 1 set "NEED_VENV=0"
)
if "%NEED_VENV%"=="1" (
  if exist ".venv" (
    echo Пересоздание .venv — окружение с другого ПК или повреждено.
    rmdir /s /q ".venv"
  )
  echo Создание виртуального окружения...
  %PYBOOT% -m venv .venv
  if errorlevel 1 (
    echo Ошибка создания venv
    if "%QUIET%"=="0" pause
    exit /b 1
  )
)

echo Установка зависимостей...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
  echo Ошибка установки пакетов
  if "%QUIET%"=="0" pause
  exit /b 1
)
if not exist config.env (
  if exist config.env.example copy /Y config.env.example config.env >nul
  echo Создан config.env — укажите SumatraPDF и принтер.
)
echo.
echo Готово. Запуск: start.bat
echo Не копируйте папку .venv на другой компьютер — там запустите setup.bat.
if "%QUIET%"=="0" pause
