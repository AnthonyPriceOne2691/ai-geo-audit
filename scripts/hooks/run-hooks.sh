#!/usr/bin/env bash
# Пускатель хуков git для той рабочей копии, где идёт коммит или push.
#
# Каталог .git/hooks у связанных деревьев один, а окружение у каждой копии своё
# (backend/.venv). Поэтому в общем каталоге — заглушки (stub.sh), а pre-commit берётся
# из копии, где git позвал хук. `pre-commit install` сюда не годится: он пишет в хук путь
# к Python одной копии, и хук ломается у всех, когда эту копию снимут.
set -euo pipefail

hook_type=${1:?тип хука: pre-commit или pre-push}
shift
root=$(git rev-parse --show-toplevel)

if [[ ! -f "$root/.pre-commit-config.yaml" ]]; then
  echo "хуки пропущены: в этой копии нет .pre-commit-config.yaml — ветка старше гейтов" >&2
  exit 0
fi

python="$root/backend/.venv/bin/python3"
if [[ ! -x "$python" ]]; then
  echo "хуки не запущены: в этой копии нет окружения backend/.venv — выполните:" >&2
  echo "  cd backend && uv sync" >&2
  exit 1
fi

cd "$root"
hooks_dir=$(cd "$(git rev-parse --git-common-dir)/hooks" && pwd)
exec "$python" -m pre_commit hook-impl --config=.pre-commit-config.yaml \
  --hook-type="$hook_type" --hook-dir "$hooks_dir" -- "$@"
