#!/usr/bin/env python3
"""Доказательства: происхождение ожидания, ревью утверждений, репро-тест.

Часть `delivery_check.py` (`delivery@1.53`). Раздел отвечает на вопрос «чем
подтверждено», а не «что заполнено»: подпись под примерами, дифф утверждений,
тест, падавший до фикса.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from delivery_base import (ROOT, ActiveCtx, field, is_placeholder,
                           read)
from delivery_base import example_ref_pattern, test_files
from delivery_decisions import signature_verdict
from delivery_history import debt_is_not_frozen, expectation_predates_tests

DIGEST = ROOT / "scripts" / "lint" / "assert_digest.sh"
DIGEST_MARK = re.compile(r"(?im)^\s*asserts_without_example:\s*(\d+|unknown)\s*$")

def check_archive_obligation(status: str, ctx: ActiveCtx, phase: str,
                             errors: list[str]) -> None:
    """Поставка уходит в archive с обязательством проверить (§13.1).

    Шов `check_evidence` (`delivery@1.57`). Отдаёт дальше только `vr` — текст
    verify-report, — и ниже он не читается: замер, а не догадка.
    """
    verify = ctx.verify
    # --- §13.1: поставка уходит в archive вместе с обязательством проверить
    # её в проде. Без этих полей «готово» проверено во всех средах, кроме
    # единственной значимой.
    if phase == "handoff":
        sig = field(status, "observe_signal")
        until = field(status, "observe_until")
        if is_placeholder(sig):
            errors.append(
                "phase=handoff: пусто observe_signal (§13.2) — что именно "
                "будет проверено в проде; «ошибок нет» сигналом не является, "
                "молчание одинаково выглядит и при не вызываемом коде"
            )
        if is_placeholder(until):
            errors.append(
                "phase=handoff: пусто observe_until (§13.1) — до какой даты "
                "наблюдаем; обязательство без срока не выполняется"
            )
        # Уровень наблюдаемости — значение проверяемое, а не декларативное
        # (урок §10.4: самозаявленное `ci-oracles: tooling` при красном CI).
        check_observability_ladder(status, sig, errors)

    if phase == "handoff" and verify.is_file():
        vr = read(verify)
        if not re.search(r"READY FOR HANDOFF", vr, re.I):
            errors.append(
                "phase=handoff: verify-report.md lacks 'READY FOR HANDOFF' verdict"
            )
        # §9.2: метрики снимаются на handoff. Без гейта ритуал не выполняется.
        if "Harness metrics" not in vr:
            errors.append(
                "phase=handoff: verify-report.md lacks the 'Harness metrics' "
                "block (§9.2 / A.10) — run: python scripts/delivery_metrics.py "
                "--base origin/main --write"
            )


def check_builder_verifier(status: str, ctx: ActiveCtx, klass: str, phase: str,
                           errors: list[str]) -> None:
    """Builder ≠ Verifier (§5.2) — заявление о независимости.

    Единственный из шести блоков, который НИЧЕГО не отдаёт дальше.
    """
    verify = ctx.verify
    # --- Builder ≠ Verifier (§5.2): не сама независимость, но явное заявление
    # о ней. Настоящее разделение обеспечивает required review в branch
    # protection (CQG §8.5); здесь ловим «сам построил, сам принял».
    if phase in {"verify", "converge", "handoff"} and verify.is_file():
        declared = field(read(verify), "Verifier") or field(status, "verifier")
        builder = field(status, "builder")
        if is_placeholder(declared):
            errors.append(
                "verify-report.md: Verifier not filled in — Builder must not "
                "accept own work (§5.2); write process:ci | agent:NAME | human:NAME"
            )
        elif (
            klass in {"M", "L"}
            and not is_placeholder(builder)
            and declared.strip().lower() == builder.strip().lower()
        ):
            errors.append(
                f"class {klass}: verifier == builder ('{declared}') — §5.2 "
                "requires a different agent/model/human, or process:ci"
            )


def check_assert_review(status: str, ctx: ActiveCtx, klass: str, phase: str,
                        errors: list[str], warnings: list[str],
                        base: str | None = None) -> None:
    """Ревью утверждений пропорционально (§3.1d ур. 3, DoD §3.2.7).
    """
    verify = ctx.verify
    # --- §3.1d уровень 3 / DoD §3.2.7: ревью утверждений пропорционально
    # непроверяемой части. `n/a` законен, только если КАЖДОЕ утверждение
    # ведёт к примеру, который человек подписал до кода; иначе ожидание
    # придумал исполнитель, и подпись обязана быть.
    if klass in {"M", "L"} and phase in {"verify", "converge", "handoff"} and verify.is_file():
        vr = read(verify)
        reviewed = field(vr, "asserts_reviewed_by")
        if is_placeholder(reviewed):
            errors.append(
                f"class {klass}: verify-report.md без asserts_reviewed_by "
                "(§3.1d уровень 3, DoD §3.2.7) — последний рубеж против "
                "неверного ожидания; вставь дайджест: "
                "bash scripts/lint/assert_digest.sh >> verify-report.md"
            )
        elif reviewed.lower().startswith("n/a"):
            check_na_digest(vr, ctx, base, errors)
        else:
            # Третье появление той же дыры. §2.2b закрыла её для `human_ok_spec`,
            # v1.30 — для `human_ok_plan`, и оба раза правка была на ПОЛЕ, а не на
            # класс полей: здесь ветка принимала ЛЮБУЮ непустую строку, поэтому
            # `asserts_reviewed_by: посмотрел сам` и `by=agent:…` проходили, а
            # автономному агенту не оставалось законного значения вовсе. Теперь
            # поле идёт через ту же развилку: подпись человека, либо `deferred`
            # с причиной, либо заслуженное `n/a` выше.
            e, w = signature_verdict("asserts_reviewed_by", reviewed, klass, phase)
            errors += e
            warnings += w


def recount_digest(base: str, spec: Path) -> tuple[str | None, str]:
    """Число дайджеста пересчётом: (`N`/`unknown` либо None, почему пересчитать нечем)."""
    if not DIGEST.is_file():
        return None, f"нет {DIGEST.relative_to(ROOT).as_posix()}"
    run = subprocess.run(["bash", str(DIGEST)], cwd=ROOT, capture_output=True, text=True,
                         errors="replace", check=False,
                         env={**os.environ, "BASE": base, "LINT_SPEC": str(spec)})
    marks = DIGEST_MARK.findall(run.stdout)
    return (marks[-1].lower(), "") if marks else (None, "дайджест не напечатал маркер")


def check_na_digest(vr: str, ctx: ActiveCtx, base: str | None, errors: list[str]) -> None:
    """`n/a` заслуживается нулём дайджеста, и у этого числа ОДИН источник (`delivery@2.15`).

    Строку `asserts_without_example` вставляет в отчёт исполнитель, и гейт ей верил:
    дайджест, устаревший после вставки или поправленный руками, давал `n/a` над
    неподписанными ожиданиями. С базой гейт пересчитывает число сам — окно то же, что у
    остальных разделов, спека — судимой поставки (у архивируемой этим диффом — архивная),
    и число отчёта обязано с пересчётом совпасть. Без базы окна нет — судится отчёт, и
    это напечатано.
    """
    digest = DIGEST_MARK.search(vr)
    if not digest:
        errors.append(
            "asserts_reviewed_by: n/a без вставленного дайджеста "
            "(нет строки 'asserts_without_example: N') — n/a надо "
            "заслужить, а не заявить (§3.1d уровень 3)"
        )
        return
    number = digest.group(1).lower()
    if base is None:
        print("дайджест §3.1d: n/a сверен только с отчётом — без --diff-base пересчёта нет")
    else:
        fresh, why = recount_digest(base, ctx.spec)
        if fresh is None:
            errors.append(f"asserts_reviewed_by: n/a, а дайджест не пересчитать ({why}) — "
                          "числу отчёта сверяться не с чем; нужна подпись human:… at=… "
                          "(§3.1d уровень 3)")
            return
        print(f"дайджест §3.1d: в отчёте {number}, пересчёт от {base}: {fresh}")
        if fresh != number:
            errors.append(f"asserts_reviewed_by: n/a, а дайджест в отчёте устарел: в отчёте "
                          f"{number}, пересчёт от {base} — {fresh}; вставь свежий: bash "
                          "scripts/lint/assert_digest.sh (§3.1d уровень 3)")
            return
    if number == "unknown":
        # `cqg@2.68`: дайджест встретил тест-файл, которого не разбирает; с `cqg@2.83` —
        # и метку, которую не с чем сверить: спеки нет. Отказ был и прежде, диагноз —
        # свой: дайджест вставлен, неизвестно число.
        errors.append(
            "asserts_reviewed_by: n/a, а дайджест не досчитал непривязанные "
            "(asserts_without_example: unknown): тест-файлы мимо масок "
            "LINT_TEST_GLOB или спеки нет по LINT_SPEC — причина над маркером; "
            "нужна подпись human:… at=… (§3.1d уровень 3)"
        )
    elif int(number) > 0:
        errors.append(
            f"asserts_reviewed_by: n/a, но {number} "
            "утверждений не ссылаются на примеры спеки — их ожидание "
            "придумал исполнитель; нужна подпись human:… at=… "
            "(§3.1d уровень 3)"
        )


def check_expectation_and_oracle(status: str, ctx: ActiveCtx, klass: str,
                                 phase: str, errors: list[str],
                                 warnings: list[str],
                                 base: str | None = None) -> None:
    """Ожидание раньше кода (§3.1d ур. 1) и реляционный оракул (§6.5 ур. 2).

    Два раздела в одной функции, хотя комментарии обещают два шва: последний
    блок ВЛОЖЕН в `if` предыдущего, и граница по маркеру его отрывает. Замер
    сказал это заранее («не парсится отдельно»), а первый заход сигнал
    проигнорировал — и сломал модуль дважды.
    """
    # ⚠ `ctx.tasks` и `ctx.verify` здесь БОЛЬШЕ НЕ БЕРУТСЯ, и пустота этой строки
    # — сама улика: функция перестала читать документы поставки о себе.
    spec = ctx.spec
    eval_smoke, implement_like = ctx.eval_smoke, ctx.implement_like
    # --- §3.1d уровень 1: ожидание обязано появиться РАНЬШЕ кода.
    # Проверяем не качество примеров (это невозможно), а сам факт, что они
    # есть и на них ссылаются тесты: иначе ожидание придумал тот же, кто писал
    # код, и все гейты ниже проверяют его согласие с самим собой.
    if klass in {"M", "L"} and phase in implement_like and spec.is_file():
        spec_text = read(spec)
        ex_ids = re.findall(r"(?m)^\s*\|\s*([A-Z]\d+)\s*\|", spec_text)

        # Корпуса «где искать». Строятся БЕЗУСЛОВНО, потому что их читают ДВЕ
        # независимые проверки — ссылки на id ниже и реляционный оракул §6.5.
        #
        # lab-12: корпус строился внутри else-ветки, и обе ветки с ошибкой роняли
        # гейт `UnboundLocalError: haystack` ВМЕСТО того, чтобы напечатать уже
        # сформулированный диагноз. Класс: **проверка, обязанная поставить
        # диагноз, умирает вместо диагноза** — исполнитель видит поломку
        # инструмента там, где ему сообщали о его собственной ошибке. Это
        # зеркало «гейта, врущего зелёным»: тот молчит, когда должен говорить,
        # этот кричит не о том. Мера — не «инициализировать переменную», а
        # держать сбор данных отдельно от разбора причин.
        #
        # ⚠ Корпусов ДВА, и это несущее (`delivery@1.72`, поле). Был один, и в
        # него входили `tasks.md` и `verify-report.md` — то есть ДОКУМЕНТЫ О
        # ПОСТАВКЕ. §6.5 ищет в корпусе `@given`, поэтому проверка гасилась
        # УПОМИНАНИЕМ декоратора в прозе отчёта: зелёное получено словом о
        # свойстве, а не свойством. Поймано так: первая редакция отчёта
        # процитировала имя искомого декоратора, ОБЪЯСНЯЯ этот самый warning, —
        # и warning исчез. Ссылки на id держали тот же дефект и заметнее: отчёт
        # перечисляет id примеров штатно, поэтому «id встречается в тестах»
        # удовлетворялось таблицей в verify-report.
        #
        # Правило к КЛАССУ: корпус проверки не смеет содержать текст, который
        # ОПИСЫВАЕТ эту проверку, — описание там неотличимо от предмета. Канон
        # это правило уже произносил в двух шагах отсюда («в них нельзя спрятать
        # неверное ожидание», §6.5) и рядом же нарушал; ловит расхождение теперь
        # не глаз, а `test_green_without_the_thing.py`.
        #
        # Границы корпусов заданы тем, что проверка СПРАШИВАЕТ. §6.5 спрашивает
        # про тест — значит только тексты тестов. Ссылки на id канон и сам
        # описывал как «в тестах ИЛИ eval-smoke» (см. ветку ниже) — значит проза
        # попала в корпус мимо собственной формулировки: `tasks.md` обещает,
        # `verify-report.md` отчитывается, а вопрос был про проверку.
        # Только текстовые исходники и не служебные каталоги (маска ловила .pyc
        # и роняла гейт, F9); где лежат — решает признак имени, а не корень.
        tests = {rel: read(ROOT / rel) for rel in test_files()}

        check_examples_and_ids(spec_text, ex_ids,
                               "".join(tests.values()) + read(eval_smoke), ctx,
                               errors, warnings, base)

        check_relational_oracle(tests, klass, warnings)


#: Признак свойства §6.5 — ГЕНЕРАТОР входов (слой 2 из четырёх), по языку тест-файла
#: (`delivery@2.11`). Поле Smart Friend Ally 18.10: знали `@given` и `fc.property`, и
#: Swift-свойство читалось «ни одного». Swift — замером их 61 тест-файла: генератор с
#: зерном пишет стандартная библиотека — тип `RandomNumberGenerator` и вытягивание
#: `using: &rng`. `seed:` отвергнут: из 13 файлов 5 сеют продукт или модель, а не входы.
RELATIONAL_MARKERS = {
    ".py": (r"@given|@hypothesis\.given", "`@given` (hypothesis)"),
    ".swift": (r"using:\s*&|\bRandomNumberGenerator\b",
               "генератор с зерном (`RandomNumberGenerator`, `using: &rng`)"),
    **dict.fromkeys((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"),
                    (r"fc\.assert|fc\.property", "`fc.property` (fast-check)")),
}


def check_relational_oracle(tests: dict[str, str], klass: str,
                            warnings: list[str]) -> None:
    """Хотя бы один реляционный оракул на M/L (§6.5 уровень 2).

    Пятый шов (`delivery@1.57`) — ВНУТРИ функции, а не по её шву: блок вложен
    в `if` соседнего раздела и берёт корпус параметром.

    ⚠ Параметр — тексты ТЕСТОВ по путям, а не `haystack`: имя корпуса и есть
    содержание правки `delivery@1.72` — проза поставки описывает искомое
    свойство теми же словами. Путь нужен с `delivery@2.11`: маркер засчитывается
    только в файле своего языка; прозы в корпусе нет с `delivery@2.14`.
    """
    # --- §6.5 уровень 2: хотя бы один реляционный оракул на M/L.
    # «Минимум один» стояло в §6.5 практикой и не принуждалось ничем —
    # та же форма, что F7. Пример со значением (`f(2) == 4`) закрепляет
    # баг, если ожидание списано с кода, и мутационный гейт его не поймает:
    # мутант будет честно убит НЕВЕРНЫМ утверждением. Свойство подделать
    # нельзя, потому что ожидаемого значения в нём нет.
    #
    # ⚠ Это проверка НАЛИЧИЯ, и она заполняется галочкой: `@given(...)` с
    # `assert True` её проходит. Ловит такое не она, а мутационный гейт —
    # декоративное свойство оставляет мутантов живыми. Поэтому требование
    # осмысленно ТОЛЬКО при работающем mutation (на macOS —
    # `brew install coreutils`, CQG §5 шаг 3), и об этом сказано в тексте
    # ошибки: иначе получим ритуал вместо оракула.
    if any(re.search(RELATIONAL_MARKERS[Path(rel).suffix][0], text)
           for rel, text in tests.items() if Path(rel).suffix in RELATIONAL_MARKERS):
        return
    # Язык без маркеров НАЗВАН, а не засчитан «нет»: не видел ≠ нет (`cqg@2.68`).
    langs = {Path(rel).suffix for rel in tests}
    looked = sorted({RELATIONAL_MARKERS[s][1] for s in langs & RELATIONAL_MARKERS.keys()}
                    or {label for _, label in RELATIONAL_MARKERS.values()})
    blind = sorted(langs - RELATIONAL_MARKERS.keys())
    warnings.append(
        f"class {klass}: ни одного реляционного оракула с генератором входов "
        f"(§6.5) — в тестах не найдено: {'; '.join(looked)}."
        + (f" Маркеров свойства для {', '.join(blind)} детектор не знает: "
           "там свойство не видно, а не отсутствует." if blind else "")
        + " Отношение на фиксированном корпусе (паритет, метаморфное без "
        "генератора) сюда не идёт: входы в нём выбрал автор. Инвариант, "
        "round-trip, идемпотентность, метаморфное отношение или differential: "
        "в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное "
        "ожидание. Один инвариант обычно ловит больше десяти тестов-значений, "
        "потому что раннер перебирает входы, о которых автор не думал. "
        "Проверь заодно, что mutation-гейт у тебя не пропускается: "
        "иначе слабое свойство (`assert result is not None`) пройдёт"
    )


def check_examples_and_ids(spec_text: str, ex_ids: list[str], refs_text: str,
                           ctx: ActiveCtx, errors: list[str],
                           warnings: list[str], base: str | None = None) -> None:
    """Блок примеров есть, id проставлены, id встречаются в тестах.

    Шестой шов (`delivery@1.57`): цепочка `if/elif/else` — это три ответа на
    один вопрос, и она уносит из вызывающей функции почти всю сложность.
    `ctx`, а не `klass` с `phase` (`delivery@2.04`): порядку §3.1d нужен ещё и
    путь спеки, а девятый аргумент пробил бы планку `max-args=8` CQG.
    """
    klass, phase = ctx.klass, ctx.phase
    if not re.search(r"(?im)^#+\s*acceptance\s+examples", spec_text):
        errors.append(
            f"class {klass}: spec.md без блока '## Acceptance examples' "
            "(§3.1d) — конкретные вход→выход, подтверждённые человеком "
            "ДО кода; проза «работает корректно» подписью не является"
        )
    elif not ex_ids:
        # Грамматика id названа ЯВНО: она не очевидна, а её нарушение
        # раньше выглядело как «примеров нет» при полной таблице примеров.
        # lab-12: арка написала id вида `EX1` и получила краш; вторая арка
        # переименовала свои id, обходя соседнюю проблему, — то есть
        # неназванное правило заставляло подстраиваться вслепую.
        errors.append(
            "spec.md: блок Acceptance examples есть, но примеров с id в "
            "нём нет (§3.1d). id читается из первой колонки таблицы и "
            "обязан быть вида ОДНА заглавная латинская буква + цифры "
            "(`A1`, `E2`); `EX1`, `A.1`, `1` и `случай-1` не считаются"
        )
    else:
        # id должны встречаться в тестах или eval-smoke: связь примера с
        # проверкой — то, что отличает обещание от отчёта о реализации.
        # ⚠ Здесь стояла ПОДСТРОКА (`i not in refs_text`), хотя соседняя
        # проверка истории ещё с lab-12 искала токен: `A6` внутри `%D0%A6`
        # засчитывался ссылкой, и пример числился покрытым без единого теста.
        # Понятие теперь одно на обе проверки — `example_ref_pattern`.
        missing = [i for i in dict.fromkeys(ex_ids)
                   if not re.search(example_ref_pattern(i), refs_text, re.M)]
        if missing:
            warnings.append(
                "acceptance-примеры без ссылки в тестах/eval-smoke: "
                f"{', '.join(missing)} (§3.1d) — пометь тест id примера в "
                "комментарии или в начале докстринга; строка, целиком равная "
                "id, и %-код ссылкой не считаются"
            )

        # Порядок «пример раньше теста» — на verify и дальше: раньше
        # тестов может просто не быть, и проверка ругалась бы на штатное
        # состояние фазы implement.
        if phase in {"verify", "converge", "handoff"}:
            test_dirs = test_files()
            # База — та же, что у breakers: порядок судится в окне ветки, иначе
            # переиспользованный id датируется чужой поставкой (`delivery@1.97`).
            # Спека архивной поставки жила в `active/` и в `archive/<slug>/`.
            spec_paths = tuple(dict.fromkeys(
                ("delivery/active/spec.md", ctx.spec.relative_to(ROOT).as_posix())))
            warnings += expectation_predates_tests(ex_ids, test_dirs, base, spec_paths)


def check_observability_ladder(status: str, sig: str, errors: list[str]) -> None:
    """Ступень наблюдаемости 2–3 требует своих полей (§13.3).

    Седьмой шов (`delivery@1.57`): вынесен не по длине, а по СЛОЖНОСТИ —
    `check_archive_obligation` укладывалась в 80 строк, но давала cx 11 при
    пороге 10. §2.1 меряет обе оси, и вторая тут была решающей.
    """
    level = field(status, "observability")
    if not is_placeholder(level) and level.strip().startswith(("2", "3")):
        # Цифра, которой НЕ предшествует латинская буква: так число порога
        # (`180мс`) отличается от id примера (`A1`) и от имени метрики
        # (`p95`). Наивный поиск любой цифры проходил на сигнале
        # «A1 подтверждён, ошибок нет» — то есть по неверной причине.
        if not re.search(r"(?<![A-Za-z])\d", sig):
            errors.append(
                f"observability: {level.strip()} заявлен, но observe_signal "
                "без метрики с числом (§13.3) — уровень 2+ означает замер "
                "ДО и порог, а не «мониторинг вроде подключён»"
            )


def check_evidence(status: str, args, errors: list[str], warnings: list[str], ctx: ActiveCtx) -> None:
    """Ожидание раньше кода, утверждения отревьюены, багфикс принёс тест."""
    phase, klass, spec, tasks, verify, eval_smoke, implement_like = (
        ctx.phase,
        ctx.klass,
        ctx.spec,
        ctx.tasks,
        ctx.verify,
        ctx.eval_smoke,
        ctx.implement_like)
    # --- Долг не должен болтаться вечно (§3.5). Проверяется ТОЛЬКО на handoff:
    # на каждом коммите это блокировало бы работу, не связанную со старым
    # долгом, и такой гейт отключают через неделю. Работать не мешаем —
    # не даём объявить сделанным.
    if phase == "handoff":
        errors.extend(debt_is_not_frozen())

    check_archive_obligation(status, ctx, phase, errors)

    check_builder_verifier(status, ctx, klass, phase, errors)

    check_assert_review(status, ctx, klass, phase, errors, warnings,
                        base=getattr(args, "diff_base", None))

    check_expectation_and_oracle(status, ctx, klass, phase, errors, warnings,
                                 base=getattr(args, "diff_base", None))

