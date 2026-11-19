#!/usr/bin/env bash
# Мета-гейт, часть третья: что из конфигов ИСПОЛНЯЕТСЯ — подключён ли гейт и
# не остался ли в конфиге контура незаполненный шаблон; итог и код выхода.
#
# Часть `check_gate_coverage.sh` (`cqg@1.86`). Отдельно от карты ролей потому,
# что это другой вопрос: там «заявление против дерева», здесь текст конфигов —
# что в нём запускается (решение «подключён ли» здесь с `cqg@2.77`) и доехал ли
# до проекта плейсхолдер. Класс замерен: `<pkg>` из шаблона `.importlinter` дожил
# до падения ЧУЖИМИ СЛОВАМИ («Could not find package '<pkg>'»), из которых
# шаблонная дыра не видна.
#
# Подключается через `source` и выполняется линейно, в том же процессе.

# --- Подключён ли гейт: факт раньше причины (`cqg@2.77`, поле voice 18.10) ----
# Строки .pre-commit-config.yaml рода $2: live — хуки, которые запускаются сами,
# и всё вне хуков; manual — хуки, чья стадия ТОЛЬКО manual (`cqg@2.77`, F4).
# Такой хук не запускается никогда: `deps-audit` был вписан с manual без CI-шага,
# аудит не исполнялся нигде (замер voice 18.10: py 14, js critical 2 / high 6), а
# мета-гейт по подстроке считал его подключённым. Стадия — своя у хука либо
# `default_stages:` файла, и только из строк вне комментария: у хука канона рядом
# лежит закомментированный `stages: [pre-push]`. Это же понятие доктор читает на
# python (`HookReaders._hook_stages`); согласие на формах pre-commit держит оракул (`cqg@3.10`).
precommit_lines() {
  awk -v want="$2" '
    function ind(s,  p) { p = match(s, /[^ ]/); return p ? p - 1 : length(s) }
    function norm(s) { sub(/[ \t]+#.*/, "", s); gsub(/[][ \t\r"\047]/, "", s); return s }
    function live(s,  a, k, i, any) {
      k = split(s, a, ",")
      for (i = 1; i <= k; i++) if (a[i] != "") { any = 1; if (a[i] != "manual") return 1 }
      return !any
    }
    function flush(  i, kind) {
      kind = (hook && !live(st != "" ? st : dflt)) ? "manual" : "live"
      # manual-строка несёт имя своего хука (`id|alias<TAB>`): его может исполнить
      # `pre-commit run <имя> --hook-stage manual` (`cqg@2.89`).
      if (kind == want) for (i = 1; i <= n; i++) print (want == "manual" ? hid "|" hal "\t" : "") buf[i]
      n = 0; hook = 0; pst = 0; st = ""; item = -1; hid = ""; hal = ""
    }
    NR == FNR {                       # проход 1: default_stages файла
      if (sub(/^default_stages:/, "")) { dflt = norm($0); blk = 1 }
      else if (blk && /^[ \t]*- /) dflt = dflt "," norm(substr($0, index($0, "-") + 1))
      else if (!/^[ \t]*(#|$)/) blk = 0
      next
    }
    FNR == 1 { item = -1 }
    /^[ \t]*#/ || /^[ \t]*$/ { next } # комментарий не исполняется
    {
      start = /^ *- +[A-Za-z_][A-Za-z0-9_-]*:/
      if (item >= 0 && (start || ind($0) <= item)) flush()
      if (start) item = ind($0)
      if (item < 0) { if (want == "live") print }
      else {
        if (/^ *(- +)?(id|alias):/) { v = $0; sub(/^[^:]*:/, "", v) }
        if (/^ *(- +)?id:/) { hook = 1; hid = norm(v) }
        if (/^ *(- +)?alias:/) hal = norm(v)
        if (/^ *(- +)?stages:/) { v = $0; sub(/^[^:]*:/, "", v); st = norm(v); pst = (st == "") }
        else if (pst && !start && /^ *- /) st = st "," norm(substr($0, index($0, "-") + 1))
        else pst = 0
        buf[++n] = $0
      }
    }
    END { if (item >= 0) flush() }' "$1" "$1"
}

unwired=()
unwired_rules=()
exempted=0
rules_checked=0

# Подключено то, что ИСПОЛНЯЕТСЯ (`cqg@2.77`): строки мест принуждения вне
# комментариев, а из pre-commit — без хуков, которые сами не запускаются
# (`precommit_lines()` выше), из CI — без workflow на одной кнопке (`button_cfg`,
# строка с именем файла). manual-хук подключён только вместе с CI-шагом,
# который зовёт его скрипт: тот попадает сюда строкой CI-конфига. Поиск —
# here-string, не `printf | grep -q`: при pipefail ранний выход grep роняет
# пишущего SIGPIPE'ом, и найденное читалось бы ненайденным.
live_cfg=""; manual_cfg=""; button_cfg=""
for cfg in "${CONFIGS[@]}"; do
  if [[ "$cfg" == .pre-commit-config.yaml ]]; then
    live_cfg+=$(precommit_lines "$cfg" live)$'\n'
    manual_cfg=$(precommit_lines "$cfg" manual)
  elif [[ "$cfg" == .github/workflows/* && "$(workflow_triggers "$cfg" | sort -u)" == workflow_dispatch ]]; then
    button_cfg+=$(awk -v f="$cfg" '!/^[ \t]*#/ { print f "\t" $0 }' "$cfg")$'\n'
  else
    live_cfg+=$(awk '!/^[ \t]*#/' "$cfg")$'\n'
  fi
done
# manual-хук, которого исполняет `pre-commit run … --hook-stage manual` из того, что
# исполняется само, — подключён (`cqg@2.89`): все без имени, по имени — один.
staged=" $(staged_runs <<< "$live_cfg" | tr '\n' ' ')"
rest=""
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  ids=${line%%$'\t'*}; hid=${ids%%|*}; hal=${ids#*|}
  if [[ "$staged" == *" * "* || "$staged" == *" $hid "* || ( -n "$hal" && "$staged" == *" $hal "* ) ]]; then
    live_cfg+="${line#*$'\t'}"$'\n'
  else
    rest+="$line"$'\n'
  fi
done <<< "$manual_cfg"
manual_cfg=$rest

# Все правила, РЕАЛЬНО вызванные в конфигах — собираем один раз и сравниваем
# точно (`grep -qxF`). Первая версия проверяла каждое имя anchored-регуляркой
# `--rule[[:space:]]+$rule([[:space:]]|$|")` и давала ложные срабатывания на
# подключённых правилах: `$` внутри группы ERE ведёт себя по-разному в
# реализациях grep — тот же класс, что §3.1a про замер портируемости.
wired_rules=$(grep -oE -- '--rule[[:space:]]+[A-Za-z0-9_-]+' <<< "$live_cfg" \
  | sed -E 's/^--rule[[:space:]]+//' | sort -u)
for path in "${gates[@]}"; do
  base=$(basename "$path")
  # Роль неприменима НА ЭТОМ СТЕКЕ — объявлено проектом, причина печатается.
  if reason=$(na_reason "$base"); then
    printf '  ○ %-30s n/a на этом стеке: %s\n' "$base" "$reason"
    exempted=$((exempted + 1))
    continue
  fi
  # Сначала ФАКТ подключения, потом причина (`cqg@2.77`, F3): у подключённого
  # причина не печатается, неподключённому место из причины не заменяет конфига.
  # -F: имя содержит точку, как regex она матчила бы лишний символ.
  if ! grep -qF -- "$base" <<< "$live_cfg"; then
    if reason=$(not_wired_reason "$base"); then
      printf '  ○ %-30s не подключён осознанно: %s\n' "$base" "$reason"
      exempted=$((exempted + 1))
    elif grep -qF -- "$base" <<< "$manual_cfg"; then
      unwired+=("$base"$'\t'"хук со stages: [manual] сам не запускается, а CI-шага с этим скриптом или с pre-commit run --hook-stage manual нет")
    elif btn=$(grep -m1 -F -- "$base" <<< "$button_cfg"); then
      unwired+=("$base"$'\t'"${btn%%$'\t'*} запускается только кнопкой (on: workflow_dispatch) — сам этот шаг не исполняется")
    elif reason=$(off_commit_reason "$base"); then
      # Имя в фигурных: bash 3.2 под UTF-8 читал первый байт «»» частью имени
      # (замер: `reason\xc2: unbound variable`).
      unwired+=("$base"$'\t'"причина канона «${reason}» называет место — гейта там нет")
    else
      unwired+=("$base")
    fi
    continue
  fi

  # --- Гранулярность ПРАВИЛА, а не файла (полевая находка F7).
  # Многоправильный скрипт считался подключённым, если в конфиге упомянут хотя
  # бы один его вызов. Правило `unstructured-log` прожило так целый релиз: было
  # в каталоге §3, в таблице §3.1 и в теле скрипта — и ни в одном конфиге.
  #
  # Поддержку флага определяем ГРЕПОМ, и только потом исполняем. Первая версия
  # этой проверки запускала `--list-rules` у каждого скрипта подряд — и повесила
  # мета-гейт: он вызвал сам себя (он тоже check_*.sh) и ушёл в рекурсию, а
  # `check_deps_audit.sh` при таком «опросе» полез бы в сеть. Опрос обязан быть
  # безопасным: только те скрипты, которые флаг объявили, и не сам вход. Его имя —
  # `$0`: здесь ЧАСТЬ, и `BASH_SOURCE[0]` с разреза `cqg@1.86` называл её, а не вход, —
  # охрана была мертва, от рекурсии спасало отсутствие флага в тексте входа (`cqg@2.88`).
  if [[ "$base" != "${0##*/}" ]] \
     && grep -qsF -- '--list-rules' "$path"; then
    case "$path" in
      *.py) rules=$(python3 "$path" --list-rules 2>/dev/null) ;;
      *)    rules=$(bash "$path" --list-rules 2>/dev/null) ;;
    esac
    while IFS= read -r rule; do
      [[ -n "$rule" ]] || continue
      # Гранулярность ПРАВИЛА и здесь: `check_ast_gate.py` несёт четыре правила,
      # и на чужом стеке неприменимы обычно все — но объявлять их надо поимённо,
      # иначе «выключить одно» неотличимо от «выключить весь скрипт».
      if reason=$(na_reason "$base:$rule"); then
        printf '  ○ %-30s n/a на этом стеке: %s\n' "$base --rule $rule" "$reason"
        exempted=$((exempted + 1))
        continue
      fi
      rules_checked=$((rules_checked + 1))
      # here-string, как у поиска скрипта выше (`cqg@2.77`, `3.02`): конвейер с grep -q
      # под pipefail ронял пишущего SIGPIPE'ом, и подключённое правило читалось нет.
      if ! grep -qxF -- "$rule" <<< "$wired_rules"; then
        unwired_rules+=("$base --rule $rule")
      fi
    done < <(printf '%s\n' "$rules")
  fi
done

# --- Незаполненный шаблон в КОНФИГЕ контура ---------------------------------
# Для полей STATUS канон это проверяет (`is_placeholder` в delivery_check), а для
# извлечённых конфигов не проверял ничего — и плейсхолдер доезжал до падения
# ЧУЖИМИ СЛОВАМИ. Развёртывание 2026-07-31: в `backend/.importlinter` остался
# `<pkg>` из шаблона, и `lint-imports` сказал «Could not find package '<pkg>'» —
# сообщение инструмента, из которого не видно, что это шаблонная дыра.
#
# Граница — где НАЧИНАЕТСЯ SHELL, а не где кончается расширение файла.
# В shell `<` — это редирект, heredoc и сравнение, поэтому *.sh не сканируются.
# Но shell живёт и ВНУТРИ yaml: блочные скаляры (`run: |`, `entry: |-`) в
# workflow и pre-commit — вложенные программы, и `<путь>` в тексте их
# diagnostic-сообщений — не поле шаблона. lab-11 (обе арки): гейт краснил
# БАЙТ-В-БАЙТ нетронутый quality.yml этого же канона за `<путь>` внутри echo,
# потому что границу держало расширение файла. Фильтруется ВЕСЬ скаляр:
# правка «на слово путь» повторила бы класс, а не закрыла его.
yaml_without_block_scalars() {
  awk '
    function ind(s,  p) { p = match(s, /[^ ]/); return p ? p - 1 : length(s) }
    {
      if (insc) {
        if ($0 ~ /^ *$/) next          # пустая строка скаляр не закрывает
        if (ind($0) > sci) next        # тело скаляра — вложенная программа
        insc = 0                       # отступ вернулся — скаляр кончился
      }
      if ($0 !~ /^[ \t]*#/ && $0 ~ /:[ \t]*[|>][+-]?[ \t]*$/) { insc = 1; sci = ind($0) }
      print                            # строка ключа — конфиг, сканируется
    }' "$1"
}
# Интерпретатор для сканера плейсхолдеров ниже: `python3`, `python` — как у
# соседних хуков. Нет ни одного — сканер молчит, и это честнее, чем grep,
# который «работал» и не находил.
PY_BIN=$(command -v python3 || command -v python || true)
if [[ -z "$PY_BIN" ]]; then
  printf '%s⚠ gate-coverage: питон не найден — проверка незаполненных шаблонов\n' "$yellow"
  printf 'в конфигах ПРОПУЩЕНА (не «шаблонов нет»). Остальные проверки идут.%s\n' "$reset"
fi
placeholders=()
for cfg in "${CONFIGS[@]}" backend/.importlinter .importlinter; do
  [[ -f "$cfg" ]] || continue
  case "$cfg" in
    *.yaml|*.yml)                      content=$(yaml_without_block_scalars "$cfg" 2>/dev/null) ;;
    *.ini|*.cfg|*.toml|*.importlinter) content=$(cat "$cfg" 2>/dev/null) ;;
    *) continue ;;
  esac
  # `<слово>` или `<слово|слово>`: форма шаблона канона. Строки-комментарии тоже
  # считаются: закомментированный плейсхолдер в конфиге — та же незаполненность.
  #
  # ⚠ ПОЧЕМУ ПИТОН, А НЕ `grep -oE`. Прежняя версия искала класс
  # `[A-Za-zА-Яа-я_]`, и **кириллический диапазон в POSIX-классе зависит от
  # локали**: GNU grep под `LC_ALL=C` (а это дефолт CI-раннеров) печатает
  # `grep: Invalid collation character` и **не находит НИЧЕГО**. То есть проверка
  # «незаполненный шаблон в конфиге» была мертва ровно в том слое, который канон
  # называет единственным неподделываемым, — и молча: stderr улетал в лог, а
  # сканер возвращал пустой список. Найдено красным CI этого репозитория
  # (8 прогонов подряд), воспроизведено локально не удалось: у macOS-grep
  # диапазон работает во всех локалях, поэтому «у меня зелено» держалось.
  # `re` в питоне работает с Unicode независимо от локали. Брат того же класса в
  # Delivery (`is_placeholder`) с самого начала на питоне — здесь остался
  # нетронутый.
  # Семантика НЕ унифицирована с Delivery намеренно: там `<pkg>` не находка
  # (иначе `List<int>` и `<100ms` в прозе краснили бы), здесь — находка, потому
  # что это дыра шаблона в конфиге (lab-10, `.importlinter`).
  [[ -n "$PY_BIN" ]] || continue
  while IFS= read -r hit; do
    [[ -n "$hit" ]] && placeholders+=("$cfg: $hit")
  done < <(printf '%s\n' "$content" | "$PY_BIN" -c '
import re, sys
pat = re.compile(r"<[A-Za-z\u0410-\u044f_][A-Za-z0-9\u0410-\u044f_|/. -]{0,30}>")
print("\n".join(sorted({m for m in pat.findall(sys.stdin.read())})))
' 2>/dev/null)
done
# exit НЕ здесь: находка уходит в общий разбор внизу, где STRICT=0 честно
# смягчает ВСЕ красные ветки. До правки exit 1 стоял ВЫШЕ проверки STRICT,
# и документированный soft-режим до этой ветки не доходил (lab-11 F5).

if (( ${#unwired[@]} == 0 && ${#missing[@]} == 0 && ${#unwired_rules[@]} == 0 && ${#placeholders[@]} == 0 )); then
  printf '%sgate-coverage: OK%s — %d скрипт(ов), подключено %d, осознанно нет %d; правил сверено %d (%d конфиг(ов))\n' \
    "$green" "$reset" "${#gates[@]}" "$(( ${#gates[@]} - exempted ))" "$exempted" \
    "$rules_checked" "${#CONFIGS[@]}"
  exit 0
fi

# Циклы под защитой (( ${#arr[@]} )): в bash 3.2 — штатном на macOS — раскрытие
# пустого массива "${arr[@]}" падает по `set -u`, даже если массив объявлен.
if (( ${#unwired[@]} )); then
  for b in "${unwired[@]}"; do
    printf '%s  ✗  %s: скрипт есть, но НЕ подключён (pre-commit / CI / merge_guard)%s\n' "$red" "${b%%$'\t'*}" "$reset" >&2
    [[ "$b" == *$'\t'* ]] && printf '       %s\n' "${b#*$'\t'}" >&2
  done
fi
if (( ${#missing[@]} )); then
  for m in "${missing[@]}"; do
    printf '%s  ✗  %s: упомянут в конфиге, но файла нет%s\n' "$red" "$m" "$reset" >&2
  done
fi
if (( ${#unwired_rules[@]} )); then
  for r in "${unwired_rules[@]}"; do
    printf '%s  ✗  %s: ПРАВИЛО не подключено (скрипт подключён, это правило — нет)%s\n' \
      "$red" "$r" "$reset" >&2
  done
fi
if (( ${#placeholders[@]} )); then
  printf '%sERROR%s: незаполненный шаблон в конфиге контура — процедура развёрнута наполовину:\n' \
    "$red" "$reset" >&2
  for p in "${placeholders[@]}"; do printf '  ✗  %s\n' "$p" >&2; done
  printf 'Подставь значения своего проекта. Иначе инструмент упадёт своими словами\n' >&2
  printf '(«Could not find package «<pkg>»»), и шаблонная дыра будет неотличима от бага.\n' >&2
fi

if [[ "$STRICT" == "0" ]]; then
  printf '%sgate-coverage: WARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2
  exit 0
fi
printf '\n%sERROR%s: гейт без подключения = гейта нет. Впиши его в `.pre-commit-config.yaml`\n' "$red" "$reset" >&2
printf 'или в CI-шаг, либо объяви роль n/a с причиной в %s (§Применимость).\n' "$NA_FILE" >&2
printf 'Сверься с таблицей §3 построчно — не собирай конфиг «по смыслу».\n' >&2
exit 1
