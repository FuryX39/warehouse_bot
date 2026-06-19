#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Установка агента печати штрихкодов ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен python3"
  exit 1
fi
need_venv=1
if [[ -x .venv/bin/python ]]; then
  if .venv/bin/python -c "import sys" >/dev/null 2>&1; then
    need_venv=0
  fi
fi
if [[ "$need_venv" -eq 1 ]]; then
  if [[ -d .venv ]]; then
    echo "Пересоздание .venv — окружение с другого ПК или повреждено."
    rm -rf .venv
  fi
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt
if [[ ! -f config.env ]] && [[ -f config.env.example ]]; then
  cp config.env.example config.env
fi
echo "Готово. Запуск: ./start.sh"
echo "Не копируйте папку .venv на другой компьютер — там запустите setup.sh."
