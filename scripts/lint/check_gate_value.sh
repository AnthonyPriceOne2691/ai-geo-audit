#!/usr/bin/env bash
# Сколько раз каждый гейт реально сработал — данные для решения «оставить или убрать».
#
# Зачем. Контур умеет только затвердевать: гейтов становится больше, меньше не
# становится никогда (Delivery §9.1a). Чтобы применить к контуру тот же ратчет,
# что к коду, нужны ДАННЫЕ, а не мнения: какой гейт ловил дефекты, а какой год
# молчит и просто тратит секунды на каждом коммите.
#
# Источник — логи красных CI-прогонов. Срабатывание — гейт УПАЛ: упал шаг, который
# его зовёт, либо хук pre-commit с итогом Failed. Имя гейта в логе срабатыванием не
# является (`cqg@3.08`): `gh` отдаёт и лог упавшего job'а целиком, а там назван каждый.
# Здесь добыча — хостинг, `gh`, перечень гейтов, логи; суждение — в части
# `gate_value_log.py`. Локальные срабатывания (коммит, push) не записаны нигде: гейту,
# который ловит до CI, прибор отвечает «по CI не судится», а не «удалить».
#
# Настройка (env):
#   CI_WORKFLOW  — какой workflow смотреть (дефолт quality)
#   RUNS         — сколько последних прогонов (дефолт 30)
#
# Нет `gh` — честно говорит, что данных нет, и не притворяется.

set -uo pipefail

CI_WORKFLOW=${CI_WORKFLOW:-quality}
RUNS=${RUNS:-30}

# SCRIPT_DIR — до `cd` в корень: `BASH_SOURCE` относителен каталога вызова (`cqg@1.86`).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# Комплект (`cqg@3.08`): разбор лога и чтение стадий хуков лежат рядом. Сверка — первой
# и той же фразой, что у гейтов-комплектов (`2.75`, `2.88`): без части прибор не считает,
# а таблица, в которой чего-то молча нет, хуже отказа.
for part in gate_value_log.py doctor_hooks.py; do
  [[ -s "$SCRIPT_DIR/$part" ]] && continue
  printf '%s✗ %s: часть %s не развёрнута в %s (нет файла или он пуст) — прибор не стартует и не считает.\n' "$red" "${0##*/}" "$part" "$SCRIPT_DIR" >&2
  printf 'Доложи её из payload канона (CQG §5.0): вход и части копируются комплектом.%s\n' "$reset" >&2
  exit 1
done

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P); unset GIT_DIR GIT_WORK_TREE   # дерево гейта, §6
[[ "${GIT_INDEX_FILE:-}" == /* && "${GIT_INDEX_FILE%/*}" -ef "$(git -C "$REPO_ROOT" rev-parse --absolute-git-dir 2>/dev/null)" ]] || unset GIT_INDEX_FILE
cd "$REPO_ROOT" || exit 1

# Вне git-репозитория инвентаря нет: «гейтов не найдено» выходило нулём (`cqg@3.13`).
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  printf '%s✗ gate-value: вне git-репозитория — судить нечего: это не «нарушений нет» (дерево %s)%s\n' "$red" "$REPO_ROOT" "$reset" >&2
  exit 1
fi

# `gh auth token` — локальная проверка учёток; `gh auth status` ходит в СЕТЬ и
# на порванном канале объявляет отсутствие логина (`cqg@1.89`, пришло полем).
# Хостинг спрашивается ПЕРЕД `gh` (`cqg@2.01`). Иначе на GitLab-машине с
# установленным и авторизованным `gh` (обычное дело у разработчика) скрипт шёл
# дальше, `gh run list` отдавал пусто, и печаталось «нет прогонов workflow
# quality» — то есть «данных нет» вместо «роль на этом хостинге не закрыта».
# Диагноз по симптому вместо причины отправляет чинить не то.
ORIGIN=$(git config --get remote.origin.url 2>/dev/null || true)
if grep -qi 'gitlab' <<< "$ORIGIN" || [[ -z "$ORIGIN" && -f .gitlab-ci.yml ]]; then
  printf '%s⚠ gate-value: проект на GitLab — роль «гейты хоть раз срабатывали» контуром не закрыта.%s\n' \
    "$yellow" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a). Закрой роль\n' >&2
  printf 'своим скриптом на `glab ci list` либо объяви её в `not-applicable.json`.\n' >&2
  exit 0
fi
# АДАПТАЦИЯ проекта (adapted.json): проверки `gh auth token` нет — она выводит токен, а на
# машинах проекта токен не выводится; вход проверяет сам `gh run list` ниже.
if ! command -v gh >/dev/null 2>&1; then
  printf '%s⚠ gate-value: нужен авторизованный gh — без него данных о срабатываниях нет.%s\n' \
    "$yellow" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a).\n' >&2
  exit 0
fi
PY=$(command -v python3 2>/dev/null || true)
if [[ -z "$PY" ]]; then
  printf '%s⚠ gate-value: нужен python3 — без него лог прогона не разобрать, данных о срабатываниях нет.%s\n' \
    "$yellow" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a).\n' >&2
  exit 0
fi

# Инвентарь гейтов = скрипты в scripts/lint (git ls-files: только отслеживаемые; `-z` —
# имя как есть, `cqg@3.01`: из кавычек сработавший кириллический гейт шёл в «удалить»).
# NOT_A_GATE — скрипты, которые лежат в scripts/lint/, но гейтами не являются:
# отчёты (не судят). Без этого списка инвентарь считал гейтом САМ ЭТОТ СКРИПТ и
# выносил ему вердикт «0 срабатываний -> нужен gate_kept_reason или удалить», то есть
# инструмент §9.1a предлагал удалить инструмент §9.1a. Заодно врал счёт молчащих.
NOT_A_GATE='check_gate_value.sh'
gates=()
while IFS= read -r p; do
  b=$(basename "$p")
  [[ -n "$b" && "$b" != "$NOT_A_GATE" ]] && gates+=("$b")
done < <(git ls-files -z 'scripts/lint/check_*.sh' 'scripts/lint/check_*.py' | tr '\0' '\n')
if (( ${#gates[@]} == 0 )); then
  echo "gate-value: гейтов не найдено" >&2; exit 0
fi

gh_err=$(mktemp) || exit 1
ids=$(gh run list --workflow "$CI_WORKFLOW" --limit "$RUNS" \
        --json databaseId,conclusion --jq '.[] | "\(.databaseId) \(.conclusion)"' 2>"$gh_err")
gh_code=$?; why="gh run list — код $gh_code: $(sed -n '1p' "$gh_err")"; rm -f "$gh_err"
if (( gh_code != 0 )); then   # АДАПТАЦИЯ: отказ вызова (нет входа, сеть) — не «нет прогонов»
  printf '%s⚠ gate-value: данных о срабатываниях нет: %s%s\n' "$yellow" "$why" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a).\n' >&2
  exit 0
fi
if [[ -z "$ids" ]]; then
  printf '%s⚠ gate-value: нет прогонов workflow "%s"%s\n' "$yellow" "$CI_WORKFLOW" "$reset" >&2
  exit 0
fi

# Лог каждого красного прогона — своим файлом: пустой (хостинг лог уже не хранит)
# обязан остаться виден в знаменателе, а в общем файле он растворялся молча.
work=$(mktemp -d) || exit 1
trap 'rm -rf "$work"' EXIT
printf '%s\n' "$ids" >"$work/runs"
while read -r id concl; do
  [[ "$concl" == "failure" && "$id" =~ ^[0-9]+$ ]] || continue
  gh run view "$id" --log-failed >"$work/$id.log" 2>/dev/null || true
done <<<"$ids"

"$PY" "$SCRIPT_DIR/gate_value_log.py" "$work" "$CI_WORKFLOW" "${gates[@]}" || {
  printf '%s✗ gate-value: разбор логов не завершился — таблицы нет, данных о срабатываниях нет.%s\n' "$red" "$reset" >&2
  exit 1
}

printf '\n%sПрочти как данные, а не как приговор:%s гейт-страховка может годами\n' "$green" "$reset"
printf 'молчать и один раз спасти прод. Но тогда это решение, записанное в\n'
printf 'delivery/STACK-ACCEPTANCE.md, а не молчаливое «пусть лежит».\n'
exit 0
