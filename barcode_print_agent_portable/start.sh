#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "Сначала запустите ./setup.sh"
  exit 1
fi
echo "Агент печати запущен. Остановка: Ctrl+C"
exec .venv/bin/python agent.py
