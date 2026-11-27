#!/usr/bin/env bash
# Доли мутационного прогона, общий порог и самопроверка счёта (`cqg@2.64`, `2.95`).
#
# Часть `check_mutation_gate.sh`, вынесена из `mutation_ts.sh` по планке 300 строк
# (Delivery §9.1a п.5): доли, счёт доли, сумма долей, порог и проверка самого счёта —
# одна тема, «сколько убито и можно ли этому верить». Подключается через `source` при
# ЛЮБОЙ половине; `MIN_KILLED`, `STRICT`, палитру и `not_judged` задаёт вход,
# `changed` — вызывающий `ts_mutation`.

# Доли прогона (`cqg@2.64`, решение владельца 18.10). `MUTATION_SHARD=k/N` оставляет
# каждый N-й файл отсортированного списка, начиная с k-го, — деление G connect. Доля
# только СЧИТАЕТ, порог судит сумма (`ts_sum`): порог на доле делает вердикт функцией
# разбиения — у G connect доля упала на 59% при общих 70% (их урок L31).
ts_shard() {
  [[ -n "${MUTATION_SHARD:-}" ]] || return 0
  if [[ ! "$MUTATION_SHARD" =~ ^([1-9][0-9]*)/([1-9][0-9]*)$ ]] \
     || (( BASH_REMATCH[1] > BASH_REMATCH[2] )); then
    printf '%s⚠ mutation: MUTATION_SHARD=%s — ждал k/N, 1 ≤ k ≤ N%s\n' "$yellow" "$MUTATION_SHARD" "$reset"
    not_judged
  fi
  changed=$(printf '%s\n' "$changed" | sort \
    | awk -v k="${BASH_REMATCH[1]}" -v n="${BASH_REMATCH[2]}" '(NR - 1) % n == k - 1')
  [[ -n "$changed" ]] && return 0
  printf '%smutation: доля %s — файлов на неё не пришлось%s\n' "$green" "$MUTATION_SHARD" "$reset"
  return 1
}

# Счёт доли — файл `shard-k-of-N.counts` («убито всего»). Пишется и тогда, когда
# доле честно нечего судить: сумма, не нашедшая файла, не отличила бы «нечего» от
# «не отчиталась». Вне долей не пишет ничего.
ts_counts() {
  [[ -n "${MUTATION_SHARD:-}" ]] || return 0
  local dir=${MUTATION_COUNTS_DIR:-reports/mutation/shards}
  mkdir -p "$dir" \
    && printf '%s %s\n' "$1" "$2" > "$dir/shard-${MUTATION_SHARD%/*}-of-${MUTATION_SHARD#*/}.counts"
}

# Порог — одно место на прогон и на сумму долей. $1 убито, $2 всего, $3 что судим,
# $4 — отчёт с выжившими, если он есть.
ts_verdict() {
  local pct
  if (( $2 == 0 )); then
    printf '%smutation/ts: %s — мутантов нет, судить нечего%s\n' "$green" "$3" "$reset"
    return 0
  fi
  pct=$(( $1 * 100 / $2 ))
  printf 'mutation/ts: %s — убито %s из %s (%s%%), порог %s%%\n' "$3" "$1" "$2" "$pct" "$MIN_KILLED"
  if (( pct < MIN_KILLED )); then
    printf '\n%sERROR%s: тесты не убивают мутантов изменённого кода — они его не проверяют.\n' \
      "$red" "$reset" >&2
    printf 'Выживший мутант = поведение, которое сьют пропустил бы в проде.\n' >&2
    [[ -n "${4:-}" ]] && printf 'Кто выжил: %s\n' "$4" >&2
    [[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; return 0; }
    return 1
  fi
  printf '%smutation/ts: OK%s\n' "$green" "$reset"
  return 0
}

# Сумма долей (`MUTATION_SUM=N`) — отдельный шаг после всех долей, Stryker ему не
# нужен. Доля без счёта — «не просудил»: сумма без неё врала бы о полноте.
ts_sum() {
  local dir=${MUTATION_COUNTS_DIR:-reports/mutation/shards} k line det=0 total=0 missing=""
  if [[ ! "$MUTATION_SUM" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s⚠ mutation: MUTATION_SUM=%s — ждал число долей N%s\n' "$yellow" "$MUTATION_SUM" "$reset"
    not_judged
  fi
  for (( k = 1; k <= MUTATION_SUM; k++ )); do
    line=$(cat "$dir/shard-$k-of-$MUTATION_SUM.counts" 2>/dev/null)
    if [[ ! "$line" =~ ^([0-9]+)\ ([0-9]+)$ ]]; then
      missing="$missing $k"
      continue
    fi
    det=$(( det + BASH_REMATCH[1] )); total=$(( total + BASH_REMATCH[2] ))
  done
  if [[ -n "$missing" ]]; then
    printf '%s⚠ mutation: нет счёта долей%s в %s — сумма неполная, гейт не судит%s\n' \
      "$yellow" "$missing" "$dir" "$reset"
    not_judged
  fi
  ts_verdict "$det" "$total" "сумма $MUTATION_SUM долей"
}

# --- Самопроверка счёта (`cqg@2.95`, слово владельца 25.10, CQG §3.7) ---------------
# Параллельный прогон засчитывает убийством ЛЮБОЕ падение теста — и падение от соседа:
# тесты, делящие базу или файл, падают от чужой сессии, а не от мутанта. Поле
# outreach-donors 02.10: 4 воркера — убито 779 из 787 (98%), один — 559 (71%), 216
# ложных. Ложь — в сторону зелёного, поэтому гейт проверяет себя на этом классе:
# выборка засчитанных перепрогоняется ПОСЛЕДОВАТЕЛЬНО, и живой мутант в ней — «прибор
# врёт». `MUTATION_WORKERS=1` гонит последовательно сразу — тогда проверять нечего.
# Ноль у ручек нарочно не предусмотрен: обход самопроверки — последовательный
# прогон, а не выключатель.
mutation_knobs() {
  local k v
  for k in MUTATION_WORKERS MUTATION_RECHECK MUTATION_RECHECK_SEC; do
    v=${!k:-}
    [[ -z "$v" || "$v" =~ ^[1-9][0-9]*$ ]] && continue
    printf '%s⚠ mutation: %s=%s — ждал целое ≥ 1%s\n' "$yellow" "$k" "$v" "$reset"
    not_judged
  done
}

# Выборка — RECHECK строк stdin равномерно по сортированному списку (или все): тот же
# прогон даёт ту же выборку, и в неё попадают разные модули, а не первые по алфавиту.
recheck_pick() {
  LC_ALL=C sort | awk -v k="$RECHECK" '{ row[NR] = $0 }
    END { n = (NR < k) ? NR : k; for (i = 0; i < n; i++) print row[int(i * NR / n) + 1] }'
}

# Вердикт самопроверки: $1 — размер выборки, $2 — код перепрогона, $3 — что перепрогон
# сказал о выборке («статус<TAB>мутант», строка на мутанта). Живой бьёт всё: он доказан
# и без конца перепрогона. Выборка, названная не целиком, не доказала ничего — иначе
# непрогнанный мутант числился бы «убитым снова».
recheck_judge() {
  local alive found
  alive=$(printf '%s\n' "$3" | awk -F'\t' '$1 ~ /^(survived|Survived|NoCoverage)$/ {
    sub(/^[^\t]*\t/, ""); gsub(/\t/, " "); print }')
  found=$(printf '%s\n' "$3" | grep -c .)
  if [[ -n "$alive" ]]; then
    printf '%s✗ mutation: ПРИБОР ВРЁТ — %s из %s убитых при параллельном прогоне ЖИВЫ\n' \
      "$red" "$(printf '%s\n' "$alive" | grep -c .)" "$1"
    printf 'при последовательном:\n'
    sed -n '1,10s/^/  /p' <<< "$alive"
    printf 'Счёт засчитал убийством падение НЕ от мутанта — от соседа (тесты делят состояние\n'
    printf 'между параллельными сессиями: база, файл, каталог, порт) или нестабильного теста —\n'
    printf 'CQG §3.7. Честный счёт — MUTATION_WORKERS=1 (дольше: подними BUDGET_SEC или дай\n'
    printf 'гейту свою джобу) либо своё состояние на сессию.%s\n' "$reset"
    not_judged
  fi
  if (( $2 == 124 )); then
    printf '%s⚠ mutation: самопроверка не уложилась в %ss — счёт параллельного прогона не проверен.\n' \
      "$yellow" "$RECHECK_SEC"
    printf 'Подними MUTATION_RECHECK_SEC, уменьши MUTATION_RECHECK или гони последовательно.%s\n' "$reset"
    not_judged
  fi
  if (( $2 != 0 || found < $1 )); then
    printf '%s⚠ mutation: самопроверка не доказала ничего — код %s, названо %s из %s (вывод выше).%s\n' \
      "$yellow" "$2" "$found" "$1" "$reset"
    not_judged
  fi
  printf 'mutation: самопроверка — %s убитых перепрогнаны последовательно, живых нет\n' "$1"
}

# Строки отчёта Stryker: файл, начало и конец (строка, колонка), мутатор, замена.
# Номера мутантов между прогонами разные, поэтому мутант узнаётся строкой. $2 = detected
# — засчитанные счётом (`Killed`, `Timeout`, как в `ts_mutation`); status — строки stdin
# со статусом из отчёта: «статус<TAB>строка».
ts_rows() {
  python3 -c '
import json, sys
want = set(sys.stdin.read().splitlines()) if sys.argv[2] == "status" else None
for path, info in json.load(open(sys.argv[1], encoding="utf-8")).get("files", {}).items():
    for m in info.get("mutants", []):
        s, e = m["location"]["start"], m["location"]["end"]
        row = "\t".join([path, str(s["line"]), str(s["column"]), str(e["line"]), str(e["column"]),
                         m.get("mutatorName", ""), json.dumps(m.get("replacement", ""), ensure_ascii=False)])
        if want is None and m.get("status") in ("Killed", "Timeout"):
            print(row)
        elif want is not None and row in want:
            print(m.get("status", "") + "\t" + row)
' "$1" "$2"
}

# Самопроверка TS-половины. Stryker перезапускается с `--concurrency 1` только на
# местах выборки — `--mutate файл:строка:колонка-строка:колонка`; колонки здесь с НУЛЯ,
# в отчёте — с единицы (замер Stryker 10: колонки отчёта дают «0 mutant(s)»). Основной
# отчёт возвращается на место: «кто выжил» читают из него.
ts_recheck() {
  local picked ranges rc again seen out
  if [[ "${MUTATION_WORKERS:-}" == "1" ]]; then
    printf 'mutation/ts: прогон последовательный (MUTATION_WORKERS=1) — самопроверка не нужна\n'
    return 0
  fi
  picked=$(ts_rows "$report" detected | recheck_pick) || {
    printf '%s⚠ mutation: в отчёте Stryker нет мест мутантов — самопроверку не собрать%s\n' \
      "$yellow" "$reset"
    not_judged; }
  [[ -n "$picked" ]] || return 0
  printf 'mutation/ts: засчитано %s из %s параллельно — выборка перепрогоняется последовательно\n' "$1" "$2"
  ranges=$(printf '%s\n' "$picked" | awk -F'\t' '{
    printf "%s%s:%d:%d-%d:%d", (NR > 1 ? "," : ""), $1, $2, $3 - 1, $4, $5 - 1 }')
  out=$(mktemp); cp "$report" "$out.main"
  ${TIMEOUT_BIN:+"$TIMEOUT_BIN" "$RECHECK_SEC"} "$STRYKER" run ${cfg:+"$cfg"} \
    ${runner:+--testRunner "$runner"} --concurrency 1 --mutate "$ranges" --reporters json >"$out" 2>&1
  rc=$?
  again=$(grep -oE 'file://[^ ]*\.json' "$out" | tail -1 | sed 's#^file://##' | python3 -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.stdin.read().strip()))')
  seen=$(printf '%s\n' "$picked" | ts_rows "${again:-нет-отчёта}" status 2>/dev/null)
  (( rc == 0 || rc == 124 )) || tail -15 "$out" >&2
  mv "$out.main" "$report"; rm -f "$out"
  recheck_judge "$(printf '%s\n' "$picked" | grep -c .)" "$rc" "$seen"
}
