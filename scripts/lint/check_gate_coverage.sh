#!/usr/bin/env bash
# МЕТА-ГЕЙТ: каждый гейт обязан быть ПОДКЛЮЧЁН, а не просто существовать.
#
# Зачем. `pre-commit run --all-files` доказывает исправность хуков, ПЕРЕЧИСЛЕННЫХ
# в конфиге, и молчит о недостающих: невключённый гейт неотличим от проходящего.
# Реальный случай (2026-07-27): скрипт jscpd был извлечён и даже адаптирован под
# язык проекта, но не вписан в .pre-commit-config.yaml — приёмка показала 7/7
# зелёных, потому что проверяла «работает ли подключённое», а не «подключено ли
# всё требуемое». Этот гейт закрывает класс ошибки, а не тот один случай.
#
# Что проверяет (в обе стороны):
#   1. Каждый скрипт в $LINT_DIR ПОДКЛЮЧЁН хотя бы в одном месте принуждения
#      (pre-commit / CI-workflow / merge_guard / Makefile / justfile): строкой,
#      которая исполняется. Комментарий и хук `stages: [manual]` без CI-шага не
#      исполняют ничего (`cqg@2.77`).
#   2. Каждый путь `scripts/...`, упомянутый в конфигах, существует на диске
#      (ловит опечатку и удалённый скрипт — «хук есть, гейта нет»).
#
# Осознанно неподключённые — инструменты, а не гейты, — перечисляются в
# not_wired_reason() С ПРИЧИНОЙ; роль, неприменимая на стеке, — в
# not-applicable.json проекта. Отсутствие в конфиге должно быть решением, а не пробелом.
#
# Настройка (env): LINT_DIR (дефолт scripts/lint), STRICT=0 — soft.
#
# Режимы:
#   check_gate_coverage.sh          # проверка; exit 1 при непокрытом гейте
#   STRICT=0 check_gate_coverage.sh # soft (warning, exit 0)

set -uo pipefail

STRICT=${STRICT:-1}
LINT_DIR=${LINT_DIR:-scripts/lint}

# SCRIPT_DIR — ДО `cd` в корень репозитория: `BASH_SOURCE` относителен каталога
# вызова, и после `cd` путь к соседним частям уже не найти (`cqg@1.86`).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# Части рядом (`cqg@1.86`). Комплект сверяется ПЕРВЫМ, раньше любой ветки, и часть —
# НЕПУСТОЙ файл (`cqg@2.75`, `2.88`): при STRICT=0 «нет мест принуждения» выходил нулём
# раньше сверки, а пустая часть вердикта давала ноль без единой строки.
for part in gate_coverage_roles.sh gate_coverage_template.sh; do
  [[ -s "$SCRIPT_DIR/$part" ]] && continue
  printf '%s✗ %s: часть %s не развёрнута в %s (нет файла или он пуст) — гейт не стартует и не судит.\n' "$red" "${0##*/}" "$part" "$SCRIPT_DIR" >&2
  printf 'Доложи её из payload канона (CQG §5.0): вход и части копируются комплектом.%s\n' "$reset" >&2
  exit 1
done

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P); unset GIT_DIR GIT_WORK_TREE   # дерево гейта, §6
[[ "${GIT_INDEX_FILE:-}" == /* && "${GIT_INDEX_FILE%/*}" -ef "$(git -C "$REPO_ROOT" rev-parse --absolute-git-dir 2>/dev/null)" ]] || unset GIT_INDEX_FILE
cd "$REPO_ROOT" || exit 1

# Вне git-репозитория отслеживаемых гейтов нет, а совет «`git add`» там не выполним
# (`cqg@3.13`). Отказ твёрдый, как у «ноль отслеживаемых»: STRICT=0 его не смягчает.
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  printf '%sgate-coverage: вне git-репозитория — судить нечего: это не «нарушений нет» (дерево %s)%s\n' "$red" "$REPO_ROOT" "$reset" >&2
  exit 1
fi

# Осознанно НЕ подключённые — причина ОБЯЗАТЕЛЬНА и печатается на каждом прогоне.
# Не список имён в комментарии: причина — это состояние проекта, которое меняется
# («тестов нет», «нет TS»), и когда оно изменится, устаревшее исключение должно
# быть видно глазами на каждом прогоне, а не лежать в коде.
# `case`, а не `declare -A`: на macOS системный bash 3.2 без ассоциативных массивов.
#
# Исключение здесь часто означает `*-oracles: weak` в delivery/active/STATUS.md
# (Delivery §3.1) — не забудь отметить там же.
#
# Этот файл в исключения НЕ вносится: мета-гейт, который сам никем не вызывается,
# — ровно та дыра, от которой он защищает, на уровень выше.
# --- Проектные исключения ролей (cqg@1.67) ------------------------------------
# `not_wired_reason()` ниже — исключения САМОГО КАНОНА, одинаковые везде. Но есть
# второй, куда более частый случай: роль каталога §3 неприменима НА ЭТОМ СТЕКЕ.
# Карта ролей §Применимость велит пометить такую строку `n/a + причина`, а
# механики для этого не было вовсе — и последствия замерены на живом
# Astro/TS-проекте: четыре python-правила и хук mypy остались подключёнными и
# красными, потому что зовут `backend/.venv/bin/python`, которого нет.
#
# Выключить их правкой ЭТОГО скрипта нельзя: payload сверяется байт-в-байт
# (`assert_digest.sh`), и проект, поправивший `not_wired_reason()`, разъезжается
# с каноном навсегда. Поэтому объявление живёт в файле ПРОЕКТА — тот же приём,
# что `canaries.json` (cqg@1.64):
#
#   scripts/lint/not-applicable.json
#   {
#     "check_ast_gate.py:silent-except": "нет python-кода: проект на Astro/TS",
#     "check_deps_audit.sh": "python-манифеста нет; npm-половина работает"
#   }
#
# Ключ — имя скрипта ИЛИ `имя:правило`: гранулярность правила обязательна, потому
# что `check_ast_gate.py` несёт четыре правила разом, и «выключить всё или
# ничего» здесь означало бы выключить всё.
#
# Причина ОБЯЗАТЕЛЬНА и печатается на каждом прогоне — как у `not_wired_reason()`,
# и по той же причине: состояние проекта меняется («тестов нет», «нет TS»), и
# устаревшее исключение должно попадаться на глаза, а не лежать в файле.
#
# Попадаться на глаза мало, если причина — «кода ещё нет» (`cqg@2.66`, решение
# владельца 18.10, образец Ally `check_na_expiry.py`). Проба Ally 03.10: первый
# закоммиченный `.swift` прошёл и мета-гейт, и доктора — роли Swift были n/a «до
# кода», код поехал мимо всех гейтов. Такое объявление пишется объектом с маской,
# и с первым файлом по маске оно ИСТЕКАЕТ — мета-гейт красный, пока человек не
# решит: подключить гейт либо переобъявить n/a с причиной, верной при коде:
#
#     "check_ast_gate.py": {"reason": "питона ещё нет", "until": "backend/*.py"}
#
# `until` — маска `git ls-files` (или список масок): индекс и неигнорируемое дерево.
# Маска называет путь ПРОДУКТА: `*.py` истёк бы сразу на питоновских скриптах самого
# контура, а `**/` в маске git лишний — `*` и так проходит `/`, и `backend/**/*.py`
# пропускает файлы верхнего уровня (замер 18.10).
NA_FILE="$LINT_DIR/not-applicable.json"
na_reasons=""
if [[ -f "$NA_FILE" ]]; then
  PY_NA=$(command -v python3 2>/dev/null || true)
  if [[ -z "$PY_NA" ]]; then
    printf '%s⚠ есть %s, но python3 не найден — проектные исключения НЕ прочитаны.\n' \
      "$yellow" "$NA_FILE"
    printf 'Это не «исключений нет»: они объявлены и не применены.%s\n' "$reset"
  else
    # stderr забирается ВМЕСТЕ с stdout, и только на ошибке. Первая редакция
    # печатала `$na_reasons` (то есть stdout) в сообщении об ошибке, а диагноз
    # питон писал в stderr — выходило «не применён: » с пустым местом там, где
    # должна стоять причина. Гейт, не назвавший причину, — тот же класс, что
    # «пропуск с чужим диагнозом»: поймано собственным тестом.
    na_out=$("$PY_NA" - "$NA_FILE" 2>&1 <<'PYNA'
import json, subprocess, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    sys.stderr.write("не разобран как JSON (%s)\n" % exc)
    raise SystemExit(2)
if not isinstance(data, dict):
    sys.stderr.write("ожидался объект {ключ: причина}\n")
    raise SystemExit(2)
lines, expired = [], []
for k, v in data.items():
    if str(k).startswith("_"):
        continue          # ключ-комментарий, не объявление роли (§5.5)
    spec = v if isinstance(v, dict) else {"reason": v}
    reason = str(spec.get("reason") or "").strip()
    if not reason:
        sys.stderr.write("%s: объявление БЕЗ причины\n" % k)
        raise SystemExit(3)
    until = spec.get("until") or []
    until = [until] if isinstance(until, str) else [str(m) for m in until]
    if until:
        hits = [h for h in subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *until],
            capture_output=True, text=True, check=False).stdout.split("\0") if h]
        if hits:
            expired.append("%s: n/a «пока нет %s» истёк — таких файлов %d, первый %s"
                           % (k, " ".join(until), len(hits), hits[0]))
            continue
    lines.append("%s\t%s\n" % (k, reason))
if expired:
    sys.stderr.write("\n".join(expired) + "\n")
    raise SystemExit(4)
sys.stdout.write("".join(lines))
PYNA
)
    na_rc=$?
    if (( na_rc == 4 )); then
      printf '%s✗ объявления n/a «до первого кода» истекли (%s):%s\n' "$red" "$NA_FILE" "$reset" >&2
      printf '%s\n' "$na_out" | sed 's/^/  /' >&2
      printf 'Код этого стека появился. Подключи гейт (убери ключ) либо переобъяви n/a\n' >&2
      printf 'с причиной, верной при коде, — уже без "until" (карта ролей §Применимость).\n' >&2
      exit 1
    fi
    if (( na_rc != 0 )); then
      printf '%s✗ %s не применён:%s\n' "$red" "$NA_FILE" "$reset" >&2
      printf '%s\n' "$na_out" | sed 's/^/  /' >&2
      printf 'Исключение без причины исключением не является (карта ролей\n' >&2
      printf '§Применимость): «не думали» и «подумали и нет» обязаны различаться\n' >&2
      printf 'в тексте, а не в тишине.\n' >&2
      exit 1
    fi
    na_reasons="$na_out"
  fi
fi

# Причина исключения по ключу `<скрипт>` или `<скрипт>:<правило>`; пусто — нет.
na_reason() {
  [[ -n "$na_reasons" ]] || return 1
  local hit
  hit=$(awk -F'	' -v k="$1" '$1 == k { print $2; exit }' <<< "$na_reasons")
  [[ -n "$hit" ]] || return 1
  printf '%s' "$hit"
}

not_wired_reason() {
  case "$1" in
    check_gate_value.sh)
      # Это не гейт, а измерительный инструмент: он ничего не запрещает — отчёт,
      # exit 0 при любом счёте. Гоняется периодически (раз в ~10 поставок) при ревизии
      # контура — Delivery §9.1a. Подключать в pre-commit/CI незачем.
      echo "измерительный инструмент, не гейт: ревизия контура раз в ~10 поставок (Delivery §9.1a)" ;;
    # Метка — только для имени из населения гейта (`check_*`). Доктор и прочие
    # инструменты без `check_` сверку не проходят вовсе: метка `contour_doctor.py`
    # не срабатывала ни разу и снята (`cqg@2.88`).
    *) return 1 ;;
  esac
}

# Причина, называющая МЕСТО вне коммита, — не исключение, а утверждение о проекте
# (`cqg@2.77`, поле voice 18.10, F3). До 2.77 она судилась РАНЬШЕ подключения:
# «CI-only на PR» печаталось у check_mutation_gate.sh, которого не было ни в одном
# конфиге, а «не подключён осознанно» — у вписанного в pre-push
# check_new_dependency.py. Теперь сначала факт: подключённый гейт причины не
# печатает, а неподключённому она не прощает — называет место, где гейта нет.
off_commit_reason() {
  case "$1" in
    check_diff_coverage.sh)
      echo "ручной DoD-шаг (§3.5): полный сьют — минуты; в CI подключён отдельным шагом" ;;
    check_mutation_gate.sh)
      echo "CI-only на PR (§8.6: минуты не влезают в бюджет pre-commit)" ;;
    check_new_dependency.py)
      # Нужен remote-ref BASE: на коммите его может не быть, и гейт молча
      # пропускал бы находки. С cqg@1.55 штатное место — pre-push, дубль — CI.
      echo "не на коммите: нужен remote-ref BASE (штатное место — pre-push, §8.5)" ;;
    check_dead_code.py) echo "не на коммите: читает всё дерево — секунды (штатное место — pre-push и CI, §3.8)" ;;
    *) return 1 ;;
  esac
}

# nullglob убирает нераскрывшиеся ГЛОБЫ, но не литеральные имена: каждый
# фиксированный путь проверяется через [[ -f ]], иначе список «мест принуждения»
# никогда не бывает пустым и проверка ниже становится мёртвым кодом.
shopt -s nullglob
CONFIGS=()
for fixed in .pre-commit-config.yaml .gitlab-ci.yml scripts/merge_guard.sh Makefile justfile; do
  [[ -f "$fixed" ]] && CONFIGS+=("$fixed")
done
CONFIGS+=(.github/workflows/*.yml .github/workflows/*.yaml)

# Что исполняется само — чтение конфигов для третьей части. Триггеры workflow — ключи
# `on:`: workflow на одной кнопке (`workflow_dispatch`) сам не исполняется (`cqg@2.88`).
workflow_triggers() {
  awk '
    function norm(s) { sub(/[ \t]+#.*/, "", s); gsub(/[][{}"\047 \t\r]/, "", s); return s }
    function key(s) { sub(/^[ \t]*(-[ \t]+)?/, "", s); sub(/[ \t]*:.*/, "", s); return norm(s) }
    { sub(/\r$/, "") }
    !on && /^["\047]?on["\047]?[ \t]*:/ {
      on = 1; v = $0; sub(/^[^:]*:/, "", v); v = norm(v)
      if (v == "") next
      n = split(v, a, ","); for (i = 1; i <= n; i++) { sub(/:.*/, "", a[i]); if (a[i] != "") print a[i] }
      exit
    }
    !on || /^[ \t]*(#|$)/ { next }
    /^[^ \t]/ { exit }
    { d = match($0, /[^ \t]/); if (!lvl) lvl = d; if (d == lvl) print key($0) }' "$1"
}
# `pre-commit run [хук] … --hook-stage manual` (stdin — исполняемые строки) ИСПОЛНЯЕТ
# manual-хуки: печатает имя хука (id или alias) либо `*` — вся ручная стадия (`cqg@2.89`).
staged_runs() {
  awk '{
    n = split($0, w, /[ \t]+/); s = 0
    for (i = 1; i < n; i++) if (w[i] ~ /(^|\/)pre-commit$/ && w[i + 1] == "run") { s = i + 2; break }
    if (!s) next
    stage = ""; hook = ""; arg = ""; files = 0
    for (j = s; j <= n; j++) {
      t = w[j]; gsub(/["\047]/, "", t); if (t == "||" || t == "&&" || t == ";" || t == "|") break
      last = sub(/[;&|]+$/, "", t)
      if (arg != "") { if (arg == "--hook-stage") stage = t; arg = "" }
      else if (t ~ /^--hook-stage=/) stage = substr(t, 14)
      else if (t ~ /^(--hook-stage|--from-ref|--to-ref|-s|-o|--source|--origin|-c|--config|--color)$/) arg = t
      else if (t ~ /^-/) files = (t == "--files")
      else if (!files && hook == "") hook = t
      if (last) break
    }
    if (stage == "manual") print (hook == "" ? "*" : hook)
  }'
}

if (( ${#CONFIGS[@]} == 0 )); then
  printf '%sERROR%s: не найдено ни одного места принуждения (.pre-commit-config.yaml / CI).\n' "$red" "$reset" >&2
  printf 'Гейты без подключения не работают — см. §5 шаги 5 и 8.\n' >&2
  # STRICT=0 обязан смягчать КАЖДУЮ красную ветку: soft-режим, который местами
  # твёрдый, — это документированное обещание, которое не выполняется (lab-11 F5).
  [[ "$STRICT" == "0" ]] && { printf '%sgate-coverage: WARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
  exit 1
fi

# git ls-files, а не файловый glob: считаем только ОТСЛЕЖИВАЕМЫЕ файлы — так же,
# как остальные гейты CQG (§4). Локальный черновик в рабочем дереве гейтом не
# является, и ругаться на него — ложное срабатывание.
gates=()
while IFS= read -r p; do
  [[ -n "$p" ]] && gates+=("$p")
done < <(git ls-files -z "$LINT_DIR/check_*.sh" "$LINT_DIR/check_*.py" | tr '\0' '\n')

# Ноль гейтов — ОШИБКА, а не «пропуск». Этот скрипт лежит в $LINT_DIR, значит CQG
# разворачивается; пустой список означает «скрипты не закоммичены» или «сломан
# LINT_DIR». Мягкий пропуск здесь был бы тем самым тихо-зелёным проходом, ради
# которого гейт и написан.
if (( ${#gates[@]} == 0 )); then
  printf '%sERROR%s: в %s нет отслеживаемых гейт-скриптов (check_*.sh|py).\n' "$red" "$reset" "$LINT_DIR" >&2
  printf 'Только что создал? `git add %s` — untracked не считается.\n' "$LINT_DIR" >&2
  exit 1
fi

# shellcheck source=/dev/null
. "$SCRIPT_DIR/gate_coverage_roles.sh"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/gate_coverage_template.sh"
# Часть вердикта выходит сама на каждой ветке. Управление вернулось — она оборвана и
# вердикта не вынесла; прежде вход кончался здесь и выходил нулём молча (`cqg@2.88`).
printf '%s✗ %s: часть gate_coverage_template.sh не развёрнута в %s целиком — гейт не стартует и не судит: она вернулась без вердикта.%s\n' "$red" "${0##*/}" "$SCRIPT_DIR" "$reset" >&2
exit 1
