#!/usr/bin/env bash
# Ставит заглушки pre-commit и pre-push в общий каталог хуков репозитория.
# Каталог один на все рабочие копии и все сессии: ставить только по слову проверяющего.
# Чужой хук на месте не перезаписывается — отказ с его путём.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Репозиторий — тот, где лежит этот скрипт, а не текущий каталог; --git-path учитывает core.hooksPath.
hooks=$(cd "$here" && cd "$(git rev-parse --git-path hooks)" && pwd)

# Сначала сверить обе цели, потом писать обе: отказ не оставляет одну заглушку без другой.
for hook in pre-commit pre-push; do
  target="$hooks/$hook"
  if [[ -e "$target" ]] && ! cmp -s "$here/stub.sh" "$target"; then
    echo "отказ: $target — чужой хук; сверьте его и уберите сами" >&2
    exit 1
  fi
done
for hook in pre-commit pre-push; do
  install -m 0755 "$here/stub.sh" "$hooks/$hook"
done
echo "заглушки стоят: $hooks/pre-commit и $hooks/pre-push"
