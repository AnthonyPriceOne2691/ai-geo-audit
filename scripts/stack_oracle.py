"""Оракул стека Ч1.5а: собранный образ делает продукт, а не только собирается.

Чистая копия `git archive HEAD` с канарейками, сборка образа, шаги примеров, уборка своего
имени в конце прогона. Имя — `ai-geo-audit-oracle-` и 10 знаков sha256 пути рабочей копии: у
двух копий разные стеки. Только стандартная библиотека; пакет сервиса не импортирует — что
нужно от пакета, спрашивает внутри собранного образа. Время в выводе — UTC с Z; наружу ничего
не публикует. Уборка при обрыве и два прогона разом — поставка «Оракул стека в CI».

Запуск из рабочей копии: `python3 scripts/stack_oracle.py`. Код 0 — все шаги прошли, 1 — есть
красные, 2 — окружение: Docker не отвечает или копия не собралась.
"""

from __future__ import annotations

import io
import json
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from oracle_guard import LockBusyError, copy_lock, guarded, say, stack_name, start_cleanup
from stack_oracle_compose import APP_UID, BREAKING_STEPS, STACK_STEPS, StepError
from stack_oracle_compose import cleanup as stack_cleanup
from stack_oracle_web import WEB_BUILD_STEPS, WEB_STACK_STEPS

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = "deploy/backend/Dockerfile"
WHITELIST = DOCKERFILE + ".dockerignore"
TARGET = "backend"
PACKAGE = "backend/src/ai_geo_audit"
#: Открытые пути белого списка контекста (A61).
OPEN_PATHS = (
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src",
    "backend/alembic.ini",
    "backend/migrations",
)
#: Канарейка в открытом каталоге под закрытым именем: её держит повторное закрытие `.env*`.
DEEP_CANARY = "backend/src/ai_geo_audit/.env.local"
#: Имена из списков проверки старта; шаг A64 сверяет их со списками пакета в образе.
BUILD_ARG_NAMES = (
    "API_TRUSTED_PROXIES",
    "DATABASE_URL",
    "JWT_SECRET",
    "KEYS_MASTER_KEY_FILE",
    "REDIS_URL",
)
DEV_TOOLS = ("uv", "pytest", "uvloop")
PROBE_DATA = "_oracle_probe.txt"
CACHE = re.compile(r"(^|/)__pycache__(/|$)|\.py[co]$")
#: Пределы команд, секунды: зависший шаг называется своим пределом, а не висит.
LIMITS = {"docker": 30, "build": 1200, "run": 120, "export": 300, "up": 300}
UP_HINT = "Docker не отвечает: поднимите Docker Desktop (open -a Docker) или службу docker"
#: Что оракул спрашивает у собранного образа: версия Python, инструменты, файлы, списки.
INSPECT = """
import importlib.util, json, os, pathlib, shutil, sys
import ai_geo_audit
from ai_geo_audit.config import startup_checks as sc
def files(root):
    return sorted(str(p.relative_to(root)) for p in pathlib.Path(root).rglob("*") if p.is_file())
print(json.dumps({
    "python": sys.version.split()[0],
    "dev": [name for name in sys.argv[1:] if importlib.util.find_spec(name)],
    "uv": shutil.which("uv"),
    "package": files(pathlib.Path(ai_geo_audit.__file__).parent),
    "workdir": files(os.getcwd()),
    "lists": sorted(set().union(*map(sc.required, ("api", "worker", "migrate")))),
}))
"""


@dataclass
class Scan:
    """Что нашлось в выгрузке: сколько файлов просмотрено, где метка, где `.env*`, их имена."""

    scanned: int = 0
    hits: list[str] = field(default_factory=list)
    envs: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)


def scan_tar(stream: IO[bytes], label: str, prefix: str = "") -> Scan:
    """Все обычные файлы выгрузки под `prefix`: метка в содержимом или в имени, `.env*` (A61)."""
    scan, mark = Scan(), label.encode()
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            name = member.name.removeprefix(prefix)
            scan.scanned += 1
            scan.names.add(name)
            member_file = archive.extractfile(member)
            data = member_file.read() if member_file else b""
            if mark in data or label in name:
                scan.hits.append(name)
            if name.rsplit("/", 1)[-1].startswith(".env"):
                scan.envs.append(name)
    return scan


def label_problems(scan: Scan) -> list[str]:
    """Метки и `.env*` быть не должно; ноль просмотренных файлов — отказ, а не «чисто»."""
    if not scan.scanned:
        return ["просмотрено файлов 0 — выгрузка пуста, судить не по чему"]
    found = [f"метка канарейки в {len(scan.hits)} файлах: {scan.hits[:3]}"] if scan.hits else []
    return found + ([f"файлы .env* в образе: {scan.envs[:3]}"] if scan.envs else [])


def context_problems(names: set[str]) -> list[str]:
    """В контексте сборки ровно пять открытых путей и ни одного файла вне них (A61)."""

    def root(name: str) -> str | None:
        return next((p for p in OPEN_PATHS if name == p or name.startswith(p + "/")), None)

    problems = [f"нет открытого пути {p}" for p in OPEN_PATHS if p not in map(root, names)]
    outside = sorted(name for name in names if root(name) is None)
    problems += [f"в контексте лишнее: {outside[:5]}"] if outside else []
    return problems + ([f"в контексте канарейка {DEEP_CANARY}"] if DEEP_CANARY in names else [])


def compare(what: str, copy: set[str], image: set[str]) -> list[str]:
    """Сверка списков файлов в обе стороны (A62)."""
    missing, extra = sorted(copy - image), sorted(image - copy)
    return [f"{what}: нет в образе — {', '.join(missing)}"] * bool(missing) + [
        f"{what}: лишние в образе — {', '.join(extra)}"
    ] * bool(extra)


def files_under(root: Path) -> set[str]:
    """Файлы копии без кэша Python и без канареек `.env*` (их держит белый список, A61)."""
    names = (str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    return {n for n in names if not CACHE.search(n) and not n.rsplit("/", 1)[-1].startswith(".env")}


_UV_PIN = re.compile(r'\buv==(?P<pip>[\w.]+)|required-version\s*=\s*"==(?P<required>[\w.]+)"')
_UV_IMAGE = re.compile(r"^FROM\s+ghcr\.io/astral-sh/uv:(?P<tag>[\w.]+)@", re.MULTILINE)


def uv_versions(root: Path) -> tuple[dict[str, str], list[str], str]:
    """Версии uv по местам — образ, файл проекта, файлы CI — и что с ними не так (A60). Файлов
    CI нет — это говорится словами: пустая сверка не выглядит зелёной."""
    image = _UV_IMAGE.search((root / DOCKERFILE).read_text(encoding="utf-8"))
    versions = {"Dockerfile": image["tag"]} if image else {}
    workflows = root / ".github" / "workflows"
    places = [root / "backend" / "pyproject.toml", *sorted(workflows.glob("*.y*ml"))]
    for place in (p for p in places if p.is_file()):
        for number, line in enumerate(place.read_text(encoding="utf-8").splitlines(), 1):
            for pin in _UV_PIN.finditer(line):
                versions[f"{place.name}:{number}"] = pin["pip"] or pin["required"]
    ci = [place for place in versions if not place.startswith(("Dockerfile", "pyproject.toml"))]
    problems = ["CI есть, а версии uv в нём нет"] * (workflows.is_dir() and not ci)
    problems += [f"версии uv разошлись: {versions}"] * (len(set(versions.values())) > 1)
    problems += ["в Dockerfile нет образа uv"] * (not image)
    return (
        versions,
        problems,
        "" if workflows.is_dir() else "файлов CI нет — сверять uv с CI не с чем",
    )


def minor(text: str) -> str:
    """Минорная версия Python из строки вида `>=3.12,<3.13` или `==3.12.*`."""
    found = re.search(r"\d+\.\d+", text)
    return found.group(0) if found else "?"


def requires_python(text: str) -> str:
    """Минорная версия из `requires-python` файла проекта или замка."""
    found = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    return minor(found.group(1)) if found else "?"


def without_cache(names: list[str]) -> set[str]:
    return {name for name in names if not CACHE.search(name)}


@dataclass
class Docker:
    """Один исполнитель команд Docker: предел на каждую команду и учёт своего для уборки."""

    containers: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def result(
        self, *args: str, limit: str = "run", **kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        """Команда Docker под пределом; `stdin` — байты на вход, `env` — окружение команды."""
        seconds, stdin = LIMITS[limit], kw.pop("stdin", None)
        try:
            return subprocess.run(  # noqa: S603 -- docker и аргументы оракула, без оболочки
                ["docker", *args],
                input=stdin,
                capture_output=True,
                timeout=seconds,
                check=False,
                **kw,
            )
        except subprocess.TimeoutExpired:
            raise StepError(f"docker {' '.join(args[:2])} не уложился в {seconds} с") from None

    def __call__(self, *args: str, limit: str = "run", check: bool = True, **kw: Any) -> str:
        done = self.result(*args, limit=limit, **kw)
        if check and done.returncode:
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
            raise StepError(f"docker {args[0]} — код {done.returncode}: {' | '.join(tail)}")
        return done.stdout.decode("utf-8", "replace")

    def cleanup(self) -> list[str]:
        """Убрать свои контейнеры и образы; что не убралось — назвать."""
        plan = [
            (("rm", "-f", "-v", c), ("ps", "-aq", "--filter", f"name=^{c}$"), c)
            for c in dict.fromkeys(self.containers)
        ]
        plan += [
            (("image", "rm", "-f", i), ("image", "ls", "-q", i), i)
            for i in dict.fromkeys(self.images)
        ]
        left = []
        for remove, probe, what in plan:
            try:
                self(*remove, limit="docker", check=False)
                if self(*probe, limit="docker", check=False).strip():
                    left.append(what)
            except StepError:
                left.append(what)
        return left


@dataclass
class Run:
    """Прогон: копия, имя стека, метка канареек, выдуманные значения сборки, узнанное шагами."""

    docker: Docker
    copy: Path
    name: str
    label: str
    args: dict[str, str]
    facts: dict[str, Any] = field(default_factory=dict)
    #: Секреты стека выдумывает оракул: пароль 24 знака hex и секрет подписи 64 знака — проверка
    #: старта в бою их принимает (A21, A3).
    password: str = field(default_factory=lambda: secrets.token_hex(12))
    jwt: str = field(default_factory=lambda: secrets.token_hex(32))

    @property
    def image(self) -> str:
        return f"{self.name}-backend:oracle"

    def export(self, image: str, prefix: str = "") -> Scan:
        """Выгрузка файлов образа через остановленный контейнер; разбор — `scan_tar`."""
        container = f"{self.name}-export"
        self.docker.containers.append(container)
        self.docker("create", "--name", container, image, limit="docker")
        tar = self.copy.parent / f"{container}.tar"
        self.docker("export", "--output", str(tar), container, limit="export")
        self.docker("rm", "-f", container, limit="docker")
        with tar.open("rb") as stream:
            scan = scan_tar(stream, self.label, prefix)
        tar.unlink()
        return scan

    def cleanup(self) -> list[str]:
        """Уборка своего имени: стек, контейнеры, образы и остаток по меткам compose."""
        return stack_cleanup(self)


def plant(copy: Path, label: str) -> None:
    """Канарейки с меткой прогона и подложенный файл данных пакета (A61, A62)."""
    places = [copy, copy / "backend", copy / PACKAGE]
    files = [place / name for place in places for name in (".env", ".env.local", ".env.prod")]
    tops = [p for p in copy.iterdir() if p.is_dir() and p.name != "backend"]
    invented = [copy / "private", copy / f"zz-{label[-6:]}", copy / ".git", copy / "backend/tests"]
    files += [top / f"canary-{label}.txt" for top in tops + invented]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{label}\n", encoding="utf-8")
    (copy / PACKAGE / PROBE_DATA).write_text("данные пакета для пробы A62\n", encoding="utf-8")


def step_build(run: Run) -> str:
    """A60, A64: сборка цели `backend` из чистой копии; выдуманные значения — в `--build-arg`."""
    args = [f"--build-arg={name}={value}" for name, value in run.args.items()]
    dockerfile = str(run.copy / DOCKERFILE)
    run.docker.images.append(run.image)
    build = ["build", "--file", dockerfile, "--target", TARGET, "--tag", run.image]
    run.docker(*build, *args, str(run.copy), limit="build")
    container = f"{run.name}-inspect"
    run.docker.containers.append(container)
    flags = ["--rm", "--name", container, "--network", "none", "--entrypoint", "python"]
    run.facts["inspect"] = json.loads(
        run.docker("run", *flags, run.image, "-c", INSPECT, *DEV_TOOLS)
    )
    config = run.docker(
        "image", "inspect", "--format", "{{json .Config}}", run.image, limit="docker"
    )
    run.facts["config"] = json.loads(config)
    return f"образ {run.image}"


def step_python(run: Run) -> str:
    """A60: Python образа — 3.12.x, та же минорная в `requires-python` и в `uv.lock`; без
    инструментов разработки."""
    facts = run.facts["inspect"]
    pyproject = (run.copy / "backend/pyproject.toml").read_text(encoding="utf-8")
    lock = (run.copy / "backend/uv.lock").read_text(encoding="utf-8")
    wanted = {requires_python(pyproject), requires_python(lock)}
    if wanted != {minor(facts["python"])} or not facts["python"].startswith("3.12."):
        raise StepError(f"Python {facts['python']}, а в pyproject и uv.lock — {sorted(wanted)}")
    if facts["dev"] or facts["uv"]:
        raise StepError(f"в образе инструменты разработки: {facts['dev']}, uv — {facts['uv']}")
    versions, problems, note = uv_versions(run.copy)
    if problems:
        raise StepError("; ".join(problems))
    uv = f"uv {set(versions.values()).pop()} в {len(versions)} местах" + (
        f"; {note}" if note else ""
    )
    return (
        f"Python {facts['python']}; requires-python и uv.lock — {minor(facts['python'])}; "
        f"{', '.join(DEV_TOOLS)} — ModuleNotFoundError, uv в PATH нет; {uv}"
    )


def step_context(run: Run) -> str:
    """A61: проба контекста — образ поверх собранного с `COPY .` из того же контекста."""
    probe = run.copy.parent / "ctx"
    probe.mkdir()
    (probe / "ctx.Dockerfile").write_text(f"FROM {run.image}\nCOPY . /ctx/\n", encoding="utf-8")
    shutil.copy(run.copy / WHITELIST, probe / "ctx.Dockerfile.dockerignore")
    image = f"{run.name}-context:oracle"
    run.docker.images.append(image)
    dockerfile = str(probe / "ctx.Dockerfile")
    run.docker("build", "--file", dockerfile, "--tag", image, str(run.copy), limit="build")
    scan = run.export(image, "ctx/")
    problems = context_problems(scan.names) + label_problems(scan)
    if problems:
        raise StepError("; ".join(problems))
    return f"в контексте {scan.scanned} файлов под пятью открытыми путями, канареек и метки нет"


def step_image_files(run: Run) -> str:
    """A61, A62: метки нет ни в одном файле образа, `.env*` нет нигде."""
    scan = run.export(run.image)
    if problems := label_problems(scan):
        raise StepError("; ".join(problems))
    return f"просмотрено файлов {scan.scanned}: метки и .env* нет"


def step_package(run: Run) -> str:
    """A62: файлы пакета в окружении образа — как в копии, с подложенным; в рабочем каталоге —
    только `alembic.ini` и `migrations/` с теми же ревизиями."""
    facts = run.facts["inspect"]
    migrations = {f"migrations/{n}" for n in files_under(run.copy / "backend/migrations")}
    package, workdir = without_cache(facts["package"]), without_cache(facts["workdir"])
    problems = compare("пакет", files_under(run.copy / PACKAGE), package)
    problems += compare("рабочий каталог", {"alembic.ini"} | migrations, workdir)
    if PROBE_DATA not in facts["package"]:
        problems.append(f"подложенного {PROBE_DATA} в образе нет")
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"пакет — {len(package)} файлов, как в копии; рабочий каталог — alembic.ini и "
        f"{len(migrations)} файлов migrations/"
    )


def step_user(run: Run) -> str:
    """A63: пользователь образа — числом, 10001."""
    user = run.facts["config"].get("User")
    if user != APP_UID:
        raise StepError(f"Config.User — {user!r}, а не {APP_UID}")
    return f"Config.User — {APP_UID}"


def step_secrets(run: Run) -> str:
    """A64: выдуманных значений `--build-arg` нет ни в истории образа, ни в `Config.Env`; нет и
    инструкций `ARG`/`ENV` с этими именами. Список имён — тот же, что у проверки старта."""
    show = ["history", "--no-trunc", "--format", "{{.CreatedBy}}"]
    history = run.docker(*show, run.image, limit="docker")
    env = json.dumps(run.facts["config"].get("Env") or [])
    leaks = [name for name, value in run.args.items() if value in history or value in env]
    named = [name for name in run.args if re.search(rf"\b(ARG|ENV)\s+{name}\b", history)]
    problems = [f"значения в образе: {leaks}"] * bool(leaks)
    problems += [f"ARG или ENV с именами: {named}"] * bool(named)
    if run.facts["inspect"]["lists"] != sorted(BUILD_ARG_NAMES):
        problems.append(f"списки проверки старта — {run.facts['inspect']['lists']}")
    if problems:
        raise StepError("; ".join(problems))
    return f"значений {len(run.args)} имён нет в истории и Config.Env"


#: Шаги: примеры, название, функция, нужен ли он следующим шагам.
STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A60 A64", "сборка из чистой копии", step_build, True),
    ("A60", "Python и инструменты", step_python, False),
    ("A61", "проба контекста", step_context, False),
    ("A61 A62", "метка и .env* в файлах образа", step_image_files, False),
    ("A62", "файлы пакета и рабочего каталога", step_package, False),
    ("A63", "пользователь образа", step_user, False),
    ("A64", "секреты сборки", step_secrets, False),
    *WEB_BUILD_STEPS,
    *STACK_STEPS,
    *WEB_STACK_STEPS,
    *BREAKING_STEPS,
]


def verdict(red: int, left: list[str]) -> tuple[int, str]:
    """Итог прогона: красные шаги или своё, оставшееся после уборки, — код 1 и «КРАСНОЕ»."""
    line = f"итог: шагов {len(STEPS)}, красных {red}"
    if left:
        return 1, f"{line}; КРАСНОЕ: после уборки осталось {', '.join(left)}"
    return (1 if red else 0), line


def copy_head(target: Path) -> None:
    """Чистая копия: `git archive HEAD` — только закоммиченное, без личных каталогов."""
    command = ["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"]
    done = subprocess.run(  # noqa: S603 -- постоянная команда git archive, без оболочки
        command, capture_output=True, timeout=LIMITS["docker"], check=True
    )
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(target, filter="data")


def run_steps(run: Run, steps: list[tuple[str, str, Callable[[Run], str], bool]]) -> int:
    """Шаги по порядку; нужный шаг красный — дальше не идём. Число красных."""
    red = 0
    for examples, title, step, needed in steps:
        try:
            say(f"{examples} {title} — ок: {step(run)}")
        except (StepError, KeyError, TypeError, ValueError) as failure:
            red += 1
            say(f"{examples} {title} — КРАСНОЕ: {failure}")
            if needed:
                break
    return red


def finish(run: Run, steps: list[tuple[str, str, Callable[[Run], str], bool]] = STEPS) -> int:
    """Шаги под обвязкой (A74): сигнал — уборка одна и код 130 или 143; иначе — итог шагов и
    остатка после уборки."""
    stopped, red, left = guarded(lambda: run_steps(run, steps), run.cleanup)
    say("уборка: " + (f"осталось {left}" if left else "своего не осталось"))
    if stopped is not None:
        say(f"прервано сигналом — код {stopped}")
        return stopped
    code, line = verdict(red or 0, left)
    say(line)
    return code


def main() -> int:
    name = stack_name(ROOT)
    try:
        with copy_lock(name):
            return run_oracle(name, Docker())
    except LockBusyError as busy:
        say(str(busy))
        return 2


def run_oracle(name: str, docker: Docker) -> int:
    """Прогон под замком копии (A75): Docker, уборка своего имени на старте (A74), копия, шаги."""
    try:
        server = docker("version", "--format", "{{.Server.Version}}", limit="docker").strip()
    except (StepError, OSError):
        say(UP_HINT)
        return 2
    say(f"оракул: стек {name}, Docker {server}")
    say(f"уборка на старте: своего имени убрано {start_cleanup(docker, name)}")
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as tmp:
        copy = Path(tmp) / "copy"
        try:
            copy_head(copy)
        except (subprocess.SubprocessError, OSError) as failure:
            say(f"копия не собралась: {type(failure).__name__}")
            return 2
        label = f"oracle-canary-{secrets.token_hex(8)}"
        plant(copy, label)
        key = Path(tmp) / "master.key"
        key.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        key.chmod(0o644)  # читает uid 10001 в контейнере; права машины переходят как есть
        values = {n: f"oracle-build-{n.lower()}-{secrets.token_hex(6)}" for n in BUILD_ARG_NAMES}
        return finish(Run(docker, copy, name, label, values))


if __name__ == "__main__":
    sys.exit(main())
