#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
  echo "Виртуальное окружение отсутствует или создано на другом ПК."
  echo "Запуск setup.sh..."
  bash "$(dirname "$0")/setup.sh"
fi
if [[ ! -f config.env ]] && [[ -f config.env.example ]]; then
  cp config.env.example config.env
fi
exec .venv/bin/python agent.py
