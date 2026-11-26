"""Базы прогона гейта мутаций (A281–A286): шаблон один раз на прогон, своя копия у каждой сессии
pytest под mutmut, уборка своего корня.

mutmut 3.8.1 на каждую порчу делает `fork` и `pytest.main`; номера потока нет — есть имя порчи в
`MUTANT_UNDER_TEST` и pid. Поэтому копия — на сессию, имя — из корня рабочей копии и pid. Сессии
главного процесса (статистика, «clean tests» с пустым именем, «forced fail») идут тем же путём;
первая из них строит шаблон кодом без порчи — у прогона гейта это статистика, у прогона
самопроверки (статистика из кэша) — список тестов. Уборку в конце прогона делает `atexit`
главного процесса: дети выходят через `os._exit` и его не зовут.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import asyncpg
import dbtools

MUTANT = "MUTANT_UNDER_TEST"
STATS = "stats"
#: Фазы главного процесса mutmut; остальные значения `MUTANT_UNDER_TEST` — имена порч у детей.
MAIN_PHASES = frozenset({"mutant_generation", STATS, "list_all_tests", "", "fail"})
#: Каталог прогона: метки сбоя копий, журнал копий, итог уборки. `mutation_py.sh` пересоздаёт
#: `mutants/` перед каждым прогоном — метки прошлого прогона до следующего не доживают.
RUN_DIR = dbtools.BACKEND / "mutants" / "db-copies"
NAME = re.compile(rf"{dbtools.PREFIX}[0-9a-f]{{10}}")
#: Корни, чей шаблон построен в этом процессе, — с pid главного процесса прогона.
BUILT: dict[Path, int] = {}


class CopyError(RuntimeError):
    """Копия базы для сессии под mutmut не создана: гейт не просудил, а не «убит»."""


def phase(environ: Mapping[str, str] = os.environ) -> str | None:
    """Имя порчи или фаза главного процесса mutmut; None — сессия не под mutmut. У «clean tests»
    имя пустое, но переменная есть."""
    return environ.get(MUTANT)


def under_mutmut(environ: Mapping[str, str] = os.environ) -> bool:
    return phase(environ) is not None


def mutmut_process() -> bool:
    """Процесс самого mutmut — главный или его форк: модуль запуска mutmut загружен. Вложенный
    pytest, унаследовавший `MUTANT_UNDER_TEST` от теста (`{**os.environ, …}`), — свежий процесс
    без него: шаблон он не строит и копии родителя не сносит."""
    return "mutmut.__main__" in sys.modules


def template_name(root: Path = dbtools.ROOT) -> str:
    """Шаблон прогона: префикс и 10 знаков sha256 строки «корень:template»."""
    return dbtools.named(f"{root.resolve()}:template")


def copy_name(pid: int | None = None, root: Path = dbtools.ROOT) -> str:
    """Копия сессии: префикс и 10 знаков sha256 строки «корень:pid»."""
    return dbtools.named(f"{root.resolve()}:{os.getpid() if pid is None else pid}")


def leftovers(rows: Iterable[tuple[str, str | None]], template: str) -> list[str]:
    """Копии своего корня: имя по формату и метка — имя своего шаблона. База разработки, база
    рабочей копии, шаблон и копии другого корня метки своего шаблона не несут."""
    return sorted(name for name, label in rows if NAME.fullmatch(name) and label == template)


async def _server() -> asyncpg.Connection:
    return await asyncpg.connect(dbtools._raw(dbtools.server_url()))


async def _databases(server: asyncpg.Connection) -> list[tuple[str, str | None]]:
    found = await server.fetch(
        "SELECT datname, shobj_description(oid, 'pg_database') AS label FROM pg_database"
    )
    return [(row["datname"], row["label"]) for row in found]


async def _drop(server: asyncpg.Connection, name: str) -> None:
    if not NAME.fullmatch(name):
        raise dbtools.RefusedError(f"сносится только база {dbtools.PREFIX}…; «{name}» не тронута")
    await server.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def sweep(root: Path = dbtools.ROOT) -> list[str]:
    """Снести копии своего корня — по метке — и свой шаблон; чужое не трогать. Что снесено из
    копий."""
    template = template_name(root)
    server = await _server()
    try:
        names = leftovers(await _databases(server), template)
        for name in [*names, template]:
            await _drop(server, name)
    finally:
        await server.close()
    return names


async def remains(root: Path = dbtools.ROOT) -> tuple[int, bool]:
    """Сколько копий своего корня на сервере и есть ли шаблон."""
    template = template_name(root)
    server = await _server()
    try:
        rows = await _databases(server)
    finally:
        await server.close()
    return len(leftovers(rows, template)), any(name == template for name, _ in rows)


async def build_template(root: Path = dbtools.ROOT) -> str:
    """Шаблон прогона: остатки оборванного прогона своего корня — снести; база — той же
    командой, что в бою (`prepare()` под именем шаблона). После — ни одного соединения к нему."""
    await sweep(root)
    template = template_name(root)
    with dbtools.active(template):
        await dbtools.prepare()
    return template


async def clone(root: Path = dbtools.ROOT, pid: int | None = None) -> str:
    """Копия шаблона: прежняя с тем же именем — от убитого процесса с тем же pid — снесена; метка
    — имя шаблона, по ней уборка; пояс — как у базовой (A9): `ALTER DATABASE … SET` в копию не
    переносится."""
    template, name = template_name(root), copy_name(pid, root)
    server = await _server()
    try:
        await _drop(server, name)
        await server.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
        await server.execute(f"COMMENT ON DATABASE \"{name}\" IS '{template}'")
        await server.execute(f"ALTER DATABASE \"{name}\" SET timezone TO '{dbtools.DB_TIMEZONE}'")
    finally:
        await server.close()
    return name


async def drop(name: str, root: Path = dbtools.ROOT, pid: int | None = None) -> None:
    """Снести свою копию или свой шаблон; чужое имя — отказ до соединения (A12, A18)."""
    if name not in (template_name(root), copy_name(pid, root)):
        raise dbtools.RefusedError(
            f"сносится только своя копия или свой шаблон {dbtools.PREFIX}…; «{name}» не тронута"
        )
    server = await _server()
    try:
        await _drop(server, name)
    finally:
        await server.close()


def journal(run_dir: Path, event: str, name: str, label: str) -> None:
    """Строка журнала копий: время, `start` или `end`, имя копии, имя порчи или фаза."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "copies.tsv").open("a", encoding="utf-8") as out:
        out.write(f"{time.time():.6f}\t{event}\t{name}\t{label}\n")


def mark(run_dir: Path, label: str, reason: str) -> None:
    """Метка сбоя: файл с именем порчи (у «clean tests» — `clean_tests`) и причиной — гейт её
    не засчитывает убитой (A285)."""
    folder = run_dir / "failed"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{label or 'clean_tests'}.txt").write_text(reason + "\n", encoding="utf-8")


def mark_refused(reason: str) -> None:
    """Сессия не началась: сервера тестов нет. В процессе самого mutmut — метка с именем порчи: гейт
    её не засчитывает убитой (A285). Вложенный pytest, унаследовавший `MUTANT_UNDER_TEST`, метки не
    кладёт: его исход судит тест, и бывает, что он нарочно идёт без сервера."""
    label = phase()
    if label is not None and mutmut_process():
        mark(RUN_DIR, label, reason)


def copies_at_once(lines: Iterable[str], until: float) -> tuple[int, int]:
    """(наибольшее число копий разом, копий без сноса в своей сессии) по журналу. Копия без
    строки сноса живёт до `until` — до уборки в конце прогона; новый клон того же имени (pid
    убитого ребёнка) снёс прежнюю. При равном времени снос идёт раньше клона."""
    opened: dict[str, float] = {}
    events: list[tuple[float, int]] = []
    abandoned = 0
    for line in lines:
        at, event, name, _ = line.split("\t", 3)
        moment = float(at)
        if event == "start":
            if name in opened:
                events.append((moment, -1))
                abandoned += 1
            opened[name] = moment
            events.append((moment, 1))
        elif opened.pop(name, None) is not None:
            events.append((moment, -1))
    abandoned += len(opened)
    events += [(until, -1)] * len(opened)
    most = current = 0
    for _, step in sorted(events):
        current += step
        most = max(most, current)
    return most, abandoned


def session_start(
    environ: Mapping[str, str] = os.environ,
    root: Path = dbtools.ROOT,
    run_dir: Path = RUN_DIR,
    at_exit: Callable[..., Any] = atexit.register,
    main: bool | None = None,
) -> None:
    """Начало сессии pytest: не под mutmut — база рабочей копии (`prepare()`); под mutmut —
    копия шаблона; первая сессия главного процесса сначала строит шаблон и ставит уборку на его
    выход — до неё детей нет, после неё шаблон не трогает никто; `main` — это процесс mutmut
    (по умолчанию — `mutmut_process()`).
    Сбой — метка с именем порчи и отказ сессии: гейт не просудил, а не «убит» (A285)."""
    label = phase(environ)
    if label is None:
        asyncio.run(dbtools.prepare())
        return
    try:
        builds = mutmut_process() if main is None else main
        if builds and label in MAIN_PHASES and root not in BUILT:
            asyncio.run(build_template(root))
            BUILT[root] = os.getpid()
            at_exit(finish_run, os.getpid(), root, run_dir)
        name = asyncio.run(clone(root))
    except Exception as exc:  # причина — в метку и в отказ сессии, ниже raise
        reason = f"копия базы не создана: {type(exc).__name__}: {exc}"
        mark(run_dir, label, reason)
        raise CopyError(f"гейт не просудил «{label or 'clean_tests'}» — {reason}") from exc
    dbtools.ACTIVE.insert(0, name)
    journal(run_dir, "start", name, label)


def session_end(
    environ: Mapping[str, str] = os.environ, root: Path = dbtools.ROOT, run_dir: Path = RUN_DIR
) -> None:
    """Конец сессии под mutmut: копия снесена. Сбой сноса — метка, а не исключение: исход сессии
    решают тесты, копию снимет уборка в конце прогона (A285, A286)."""
    label = phase(environ)
    if label is None or not dbtools.ACTIVE:
        return
    name = dbtools.ACTIVE.pop(0)
    try:
        asyncio.run(drop(name, root))
    except Exception as exc:  # причина — в метку; исход сессии решают тесты
        mark(run_dir, label, f"копия {name} не снесена: {type(exc).__name__}: {exc}")
        return
    journal(run_dir, "end", name, label)


def finish_run(main: int, root: Path = dbtools.ROOT, run_dir: Path = RUN_DIR) -> None:
    """Уборка в конце прогона — только в главном процессе mutmut: копии своего корня по метке и
    шаблон; итог — строкой в `report.txt` (A281, A286). Ребёнок, вышедший не через `os._exit`,
    базы соседей не сносит."""
    if os.getpid() != main:
        return
    try:
        swept = asyncio.run(sweep(root))
        left, template_left = asyncio.run(remains(root))
    except Exception as exc:  # итог — в отчёт: исход гейта считает mutmut
        line = f"базы гейта: уборка в конце прогона не удалась — {type(exc).__name__}: {exc}"
    else:
        path = run_dir / "copies.tsv"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        most, abandoned = copies_at_once(lines, time.time())
        template = "остался" if template_left else "снят"
        line = (
            f"базы гейта: копий разом — наибольшее {most}, и шаблон; без сноса в своей сессии — "
            f"{abandoned}, уборка сняла {len(swept)}; после прогона копий {left}, шаблон {template}"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.txt").write_text(line + "\n", encoding="utf-8")
