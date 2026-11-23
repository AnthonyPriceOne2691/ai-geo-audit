#!/usr/bin/env bash
# Вердикт мутационного гейта: что считать доказательством и когда краснеть.
#
# Часть `check_mutation_gate.sh` (`cqg@1.86`). Отделено от запуска сознательно:
# главный урок этого гейта — код возврата инструмента вердиктом НЕ является, и
# судить можно только по счётчикам. Держать это рядом с логикой запуска значило
# смешивать «что мы сделали» с «что это доказывает».

if ( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" export-cicd-stats >/dev/null 2>&1 ) && [[ -f "$stats" ]]; then
  killed=$(grep -oE '"killed"[[:space:]]*:[[:space:]]*[0-9]+' "$stats" | grep -oE '[0-9]+$' | sed -n 1p)
  survived=$(grep -oE '"survived"[[:space:]]*:[[:space:]]*[0-9]+' "$stats" | grep -oE '[0-9]+$' | sed -n 1p)
fi
if [[ -z "$killed" && -z "$survived" ]]; then
  killed=$(grep -oE '🎉 *[0-9]+' "$out" | grep -oE '[0-9]+' | tail -1)
  survived=$(grep -oE '🙁 *[0-9]+' "$out" | grep -oE '[0-9]+' | tail -1)
fi
if [[ -z "$killed" && -z "$survived" ]]; then
  res=$( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results 2>/dev/null )
  killed=$(printf '%s\n' "$res" | grep -oE '🎉 *[0-9]+' | grep -oE '[0-9]+' | sed -n 1p)
  survived=$(printf '%s\n' "$res" | grep -oE '🙁 *[0-9]+' | grep -oE '[0-9]+' | sed -n 1p)
fi

# Население — по `.meta` mutmut (`cqg@3.18`). Ключи мутантов файла он пишет туда ДО фильтра
# по глобам: это связь «файл → ключи», не зависящая от того, как гейт выводит ключ из пути.
# До `3.18` гейт выводил его иначе, чем mutmut (раскладка `src/`), глоб не выбирал ничего, и
# «судить нечего» выходило нулём — зелёное без предмета. Законный ноль — `.meta` без ключей
# (константы, только импорты); файл с ключами, из которых глоб не выбрал ни одного, и файл
# без `.meta` (mutmut его не мутирует) — не просужены. Поле не нашлось — код 3, не «ключей нет».
meta_keys() {
  awk '{ t = t $0 } END {
    i = index(t, "\"exit_code_by_key\""); if (!i) exit 3
    t = substr(t, i + 18); t = substr(t, index(t, "{") + 1); t = substr(t, 1, index(t, "}") - 1)
    n = split(t, kv, ",")
    for (j = 1; j <= n; j++) if (match(kv[j], /"[^"]*"/)) print substr(kv[j], RSTART + 1, RLENGTH - 2)
  }' "$1"
}
py_population() {
  local i f meta keys key n hit files=0 mutants=0 empty=0
  local -a lost=()
  for (( i = 0; i < ${#mut_files[@]}; i++ )); do
    f=${mut_files[i]}; meta="$REPO_ROOT/$MUT_CWD/mutants/${f#"$MUT_CWD"/}.meta"
    if [[ ! -f "$meta" ]] || ! keys=$(meta_keys "$meta"); then
      lost+=("$f — нет .meta с ключами: файл вне source_paths mutmut либо формат незнаком"); continue
    fi
    n=0; hit=0
    while IFS= read -r key; do
      [[ -n "$key" ]] || continue
      n=$((n + 1))
      # shellcheck disable=SC2053  # справа глоб, как его читает fnmatch mutmut
      [[ "$key" == ${mut_globs[i]} ]] && hit=$((hit + 1))
    done <<< "$keys"
    if (( n == 0 )); then empty=$((empty + 1))
    elif (( hit == 0 )); then lost+=("$f — ключей $n (первый $(sed -n 1p <<< "$keys")), глоб ${mut_globs[i]} не выбрал ни одного")
    else files=$((files + 1)); mutants=$((mutants + hit)); fi
  done
  printf 'mutation: судил файлов %s из %s, мутантов %s; без мутантов %s\n' "$files" "${#mut_files[@]}" "$mutants" "$empty"
  (( ${#lost[@]} == 0 )) && return 0
  printf '%s⚠ mutation: изменённые файлы не просужены — мутантов, выбранных гейтом, у них нет:\n' "$yellow"
  printf '  %s\n' "${lost[@]}"
  printf 'Это не законный ноль. Глоб гейта — путь файла от %s без `src/`.%s\n' "$MUT_CWD" "$reset"
  return 1
}

# Ни один источник не дал числа. Причин несколько, и они РАЗНЫЕ: ключи мутантов не
# совпали с путями импорта, mutmut не смог запустить pytest, мутанты не покрыты,
# mutmut упал (rc != 0), формат вывода незнаком. Каждая печатается с уликами, и ни
# одна не притворяется диагнозом «нет тестов».
# Один разборщик на ВСЕ исходы «гейт не судит»: конкретная причина обязана бить
# общую, а улика печатается всегда. До lab-12 конкретные причины были распределены
# по двум местам, и та, что стояла позже, была недостижима: mutmut выходил rc=1,
# срабатывала общая ветка «завершился с кодом 1», а настоящая причина (не смог
# запустить pytest / мутанты не покрыты) не называлась никогда. Тест поймал это
# на первой же попытке — ветку писали, а достижимость не проверили.
undecided() {
  local honest=""
  # Расхождение ключей — самый вероятный исход на непривычной раскладке, и апстрим
  # называет его сам. Ветка стоит ПЕРВОЙ, потому что mutmut при этом выходит rc=0:
  # общая ветка «завершился с кодом N» его бы не поймала, а «мутанты не
  # сгенерированы» соврала бы про тесты.
  if grep -q 'Filtered for specific mutants, but nothing matches' "$out"; then
    # Глобы не выбрали ни одного мутанта. Честное «нечего судить» (константы, только
    # импорты, `__all__`) — только когда и по `.meta` мутантов у изменённых файлов нет.
    if py_population; then
      printf '%smutation: изменённые файлы не дали мутантов (константы? только импорты?)\n' "$yellow"
      printf 'Область была: %s. Гейт не судит — судить нечего.%s\n' "${mut_globs[*]}" "$reset"
      honest=1
    fi
  elif grep -q 'none match any mutant key' "$out"; then
    printf '%s⚠ mutation: путь импорта пакета не совпал с путём файла — гейт не судит\n' "$yellow"
    printf '(тесты тут ни при чём). mutmut выводит ключ мутанта ИЗ ПУТИ ФАЙЛА, и он обязан\n'
    printf 'совпасть с тем, как тесты импортируют пакет. Гейт уже запускается из `%s`,\n' "$MUT_CWD"
    printf 'то есть ждёт `import %s...`. Расхождение обычно даёт лишний `pythonpath` или\n' "$MUT_SRC"
    printf 'sys.path-инъекция в conftest. Ключи, которые не сошлись, — в выводе ниже.%s\n' "$reset"
  elif grep -qE 'BadTestExecutionCommandsException.*::[^ ]*\[[^]]*\\(u[0-9a-f]{4}|x[0-9a-f]{2}|U[0-9a-f]{8})' "$out"; then
    # Узел с не-ASCII в `ids` pytest печатает экранированным, и второй `pytest.main` процесса (так
    # зовёт mutmut) его не находит — код 4, pytest 8.4–9.2: гейт не судил ничего (`cqg@3.18`).
    printf '%s⚠ mutation: pytest не нашёл узел теста, который сам назвал, — гейт не судит (тесты тут ни при чём).\n' "$yellow"
    printf 'Узел с не-ASCII в параметре напечатан экранированным, обратно его не выбрать. Ключ фрагмента\n'
    printf '(CQG Приложение B): disable_test_id_escaping_and_forfeit_all_rights_to_community_support = true%s\n' "$reset"
  elif grep -qE 'BadTestExecutionCommandsException|Failed to run pytest' "$out"; then
    printf '%s⚠ mutation: mutmut не смог ЗАПУСТИТЬ pytest — гейт не судит (тесты тут ни при чём).\n' "$yellow"
    printf 'mutmut копирует `source_paths` в `%s/mutants/` и гоняет pytest ИЗ НЕГО,\n' "$MUT_CWD"
    printf 'поэтому путь до тестов обязан резолвиться оттуда. `also_copy` в дефолте берёт\n'
    printf '`tests/` относительно `%s` — если тесты лежат иначе, добавь их туда.\n' "$MUT_CWD"
    printf 'Диагноз апстрима включается `debug=true` в [tool.mutmut].%s\n' "$reset"
  elif grep -qE 'could not find any test case|do not cover any code' "$out"; then
    printf '%s⚠ mutation: pytest запустился, но НИ ОДИН мутант не покрыт тестами — гейт не судит.\n' "$yellow"
    printf 'Тесты импортируют установленный пакет, а не мутированную копию из\n'
    printf '`%s/mutants/` — измерено на mutmut %s. Это НЕ «тесты ничего не\n' "$MUT_CWD" "$MUT_VER"
    printf 'утверждают»: см. §3.7.%s\n' "$reset"
  elif (( rc != 0 )); then
    printf '%s⚠ mutation: mutmut %s завершился с кодом %s — гейт не судит, вывод ниже%s\n' \
      "$yellow" "$MUT_VER" "$rc" "$reset" >&2
  elif [[ -n "${1:-}" ]]; then
    printf '%s⚠ mutation: мутанты не сгенерированы (пустые файлы? нет тестов?) — гейт не судит%s\n' \
      "$yellow" "$reset"
  else
    printf '%s⚠ mutation: счётчики не найдены в выводе mutmut %s — формат незнаком,\n' \
      "$yellow" "$MUT_VER"
    printf 'гейт не судит (это НЕ «мутантов нет»). Вывод ниже.%s\n' "$reset" >&2
  fi
  tail -15 "$out" >&2
  # Честное «судить нечего» — тихо; любой другой исход — гейт НЕ судил при
  # объявленной роли, и это не зелёное (`cqg@2.46`).
  [[ -n "$honest" ]] && { ts_counts 0 0; exit 0; }   # адаптация A289: у доли — счёт «0 0»
  not_judged
}

if [[ -z "$killed" && -z "$survived" ]]; then
  undecided
fi

killed=${killed:-0}; survived=${survived:-0}
total=$((killed + survived))

if (( total == 0 )); then
  # Счётчики есть, но нули: тот же разборщик, аргумент включает ветку
  # «мутанты не сгенерированы» как ПОСЛЕДНЮЮ, а не как единственную.
  undecided empty
fi
py_population || not_judged

pct=$(( killed * 100 / total ))
printf 'mutation: убито %d из %d (%d%%), выжило %d\n' "$killed" "$total" "$pct" "$survived"

# Самопроверка счёта (`cqg@2.95`, общая часть — `mutation_shard.sh`). Мутанта,
# названного по имени, mutmut гонит заново, даже убитого (так с 3.0), и итог каждого
# пишет в `.meta` сразу — `results` читает его и после потолка. Выборка — из убитых:
# проверяется то, что счёт засчитал. Зовётся только на зелёном: ложь — в его сторону.
py_recheck() {
  local sample rc m
  local -a names=()
  if [[ "${MUTATION_WORKERS:-}" == "1" ]]; then
    printf 'mutation: прогон последовательный (MUTATION_WORKERS=1) — самопроверка не нужна\n'
    return 0
  fi
  (( killed > 0 )) || return 0
  sample=$( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results --all true 2>/dev/null \
    | awk -F': ' '$2 == "killed" { sub(/^ +/, "", $1); print $1 }' | recheck_pick )
  while IFS= read -r m; do [[ -n "$m" ]] && names+=("$m"); done <<< "$sample"
  if (( ${#names[@]} == 0 )); then
    printf '%s⚠ mutation: убито %s, а `mutmut results` не назвал ни одного убитого —\n' "$yellow" "$killed"
    printf 'самопроверку не собрать, счёт не проверен.%s\n' "$reset"
    not_judged
  fi
  ( cd "$REPO_ROOT/$MUT_CWD" && ${TIMEOUT_BIN:+"$TIMEOUT_BIN" "$RECHECK_SEC"} \
      "$MUTMUT" run --max-children 1 "${names[@]}" ) >"$out" 2>&1
  rc=$?
  db_marks   # адаптация (A285, A287): сбой копии базы в самопроверке — «не просудил», а не «убит снова»
  (( rc == 0 || rc == 124 )) || tail -15 "$out" >&2
  recheck_judge "${#names[@]}" "$rc" "$( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results --all true \
    2>/dev/null | awk -F': ' 'NR == FNR { pick[$0]; next } { sub(/^ +/, "", $1) }
      ($1 in pick) { print $2 "\t" $1 }' <(printf '%s\n' "${names[@]}") - )"
}

# Адаптация (scripts/lint/adapted.json, A289): доля только считает, как у TS-половины —
# самопроверка всегда (ложь параллели — в сторону зелёного суммы), счёт в
# `shard-k-of-N.counts`, порог судит сумма (`MUTATION_SUM`).
if [[ -n "${MUTATION_SHARD:-}" ]]; then
  py_recheck
  ts_counts "$killed" "$total"
  printf 'mutation: доля %s — убито %s из %s; порог судит сумма долей (MUTATION_SUM)\n' \
    "$MUTATION_SHARD" "$killed" "$total"
  exit 0
fi

if (( pct >= MIN_KILLED )); then
  py_recheck
  printf '%smutation: OK%s\n' "$green" "$reset"
  exit 0
fi

printf '\n%sERROR%s: убито %d%% мутантов, цель ≥%d%%.\n' "$red" "$reset" "$pct" "$MIN_KILLED" >&2
# Улика обязательна: красный гейт без списка выживших учит искать не там.
# Список даёт `results`, а НЕ `show`: у 3.x `show` требует имя мутанта
# (`Error: Missing argument 'MUTANT_NAME'`), и голый вызов печатал только эту
# ошибку — под `2>/dev/null` не печатал вообще ничего. То есть обвинение
# предъявлялось без улик всё время существования ветки.
printf 'Выжившие мутанты = поведение, которое тесты НЕ проверяют:\n' >&2
( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results 2>/dev/null ) | grep -F survived | sed -n 1,30p >&2
printf 'Разобрать конкретный: cd %s && mutmut show <имя-из-списка>\n' "$MUT_CWD" >&2
printf 'Починка: добавить утверждения на выжившие случаи, а не поднимать порог.\n' >&2
[[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
exit 1
