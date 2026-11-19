#!/usr/bin/env bash
# Ставит заглушки pre-commit и pre-push в общий каталог хуков репозитория.
# Каталог один на все рабочие копии и все сессии: ставить только по строке проверяющему.
# Чужой хук на месте не перезаписывается — отказ с его путём.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(git rev-parse --show-toplevel)"
hooks=$(cd "$(git rev-parse --git-common-dir)/hooks" && pwd)

for hook in pre-commit pre-push; do
  target="$hooks/$hook"
  if [[ -e "$target" ]] && ! cmp -s "$here/stub.sh" "$target"; then
    echo "отказ: $target — чужой хук; сверьте его и уберите сами" >&2
    exit 1
  fi
  install -m 0755 "$here/stub.sh" "$target"
done
echo "заглушки стоят: $hooks/pre-commit и $hooks/pre-push"
