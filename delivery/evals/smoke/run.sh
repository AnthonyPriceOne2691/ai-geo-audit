#!/usr/bin/env bash
# Сквозные проверки smoke (§6.1): сценарии таблицы README подряд, код 1 на первом сбое.
# Помощник — backend/tests/smoke_run.py на интерпретаторе окружения копии. PYTHONPATH —
# backend/src корня: в дереве merge_guard окружение одолжено у основной папки, и без него
# сценарии судили бы её пакет (спека smoke, A377). exec — у run.sh и помощника один pid:
# SIGINT и SIGTERM доходят до уборки без посредника.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
python="$root/backend/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "окружение: нет backend/.venv — cd backend && uv sync --frozen"
  exit 1
fi
export PYTHONPATH="$root/backend/src"
exec "$python" "$root/backend/tests/smoke_run.py" "$@"
