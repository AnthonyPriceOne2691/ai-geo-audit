#!/usr/bin/env bash
# Заглушка хука в общем каталоге .git/hooks — одна на все рабочие копии.
# Ставит её scripts/hooks/install-stubs.sh под именами pre-commit и pre-push.
# Пускатель у каждой копии свой: нет его — ветка старше пускателя, хук пропускается.
# Доктор узнаёт установленный хук по слову «pre-commit» в тексте файла — слово не убирать.
launcher="$(git rev-parse --show-toplevel)/scripts/hooks/run-hooks.sh"
if [[ ! -x "$launcher" ]]; then
  echo "хуки пропущены: в этой копии нет scripts/hooks/run-hooks.sh — ветка старше пускателя" >&2
  exit 0
fi
exec "$launcher" "$(basename "$0")" "$@"
