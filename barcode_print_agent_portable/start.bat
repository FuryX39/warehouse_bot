@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto NEED_SETUP
".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 goto NEED_SETUP
goto RUN

:NEED_SETUP
echo Виртуальное окружение отсутствует или создано на другом ПК.
echo Запуск setup.bat...
call "%~dp0setup.bat" /q
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo setup.bat не создал .venv
  pause
  exit /b 1
)

:RUN
if not exist config.env if exist config.env.example copy /Y config.env.example config.env >nul
echo Агент печати: откроется окно статуса. Закрытие окна останавливает агент.
".venv\Scripts\python.exe" agent.py
if errorlevel 1 pause
