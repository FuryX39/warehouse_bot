#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Установка агента печати штрихкодов ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен python3"
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt
if [[ ! -f config.env ]] && [[ -f config.env.example ]]; then
  cp config.env.example config.env
fi
echo "Готово. Запуск: ./start.sh"
